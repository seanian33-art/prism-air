"""Google Patents에서 특허 번호로 실제 서지·초록·청구항을 가져온다.

특허 번호만 LLM에 넘기면 모델이 특허 내용을 지어낼 수 있으므로(번호로 특허를 조회할 수 없음),
반드시 원문을 가져와 텍스트로 전달한다. 조회에 실패하면 PatentLookupError로 알리고 내용을 만들지 않는다.

페이지 구조 (확인: 2026-11):
- https://patents.google.com/patent/{번호}/{en|ko}  → 없는 번호는 404
- <meta name="DC.title" content="...">                → 발명의 명칭
- <section itemprop="abstract"> ... </section>         → 요약
- <section itemprop="claims"> <div num="N" class="claim"> <div class="claim-text">…  → 청구항
- KR 특허는 /ko 로 조회하면 번역 없는 한국어 원문을 준다.
"""

import html
import re

import requests

TIMEOUT_SEC = 20
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PRISM-Air/1.0"}


class PatentLookupError(Exception):
    """특허 조회 실패 (사용자에게 그대로 보여줄 수 있는 메시지)."""


def normalize_number(raw: str) -> str:
    return re.sub(r"[\s\-/,.]", "", raw or "").upper()


def candidate_numbers(raw: str) -> list[str]:
    """사용자 입력을 Google Patents 공개/등록 번호 후보로 변환한다.

    - US11234567B2, US11234567, EP1234567A1 …          → 그대로
    - KR10-2023-0012345 (국내 공개·출원 표기 10-YYYY-NNNNNNN) → KR20230012345A
    - KR10-2345678 (국내 등록 표기 10-NNNNNNN)            → KR102345678B1
    """
    n = normalize_number(raw)
    if not n:
        return []
    if not re.match(r"^[A-Z]{2}", n):
        raise PatentLookupError("특허 번호 앞에 국가 코드를 붙여 주세요. (예: US11234567B2, KR10-2023-0012345)")
    cands = [n]
    m = re.fullmatch(r"KR10(\d{4})(\d{7})", n)
    if m:  # 10-YYYY-NNNNNNN → 공개번호
        cands = [f"KR{m.group(1)}{m.group(2)}A", n]
    m = re.fullmatch(r"KR10(\d{7})", n)
    if m:  # 10-NNNNNNN → 등록번호
        cands = [f"KR10{m.group(1)}B1", f"KR10{m.group(1)}B2", n]
    return cands


def _clean(fragment: str) -> str:
    """HTML 조각 → 읽기 좋은 텍스트 (번역 페이지의 원문 span 제거, 줄바꿈 보존)."""
    fragment = re.sub(r'<span class="google-src-text">.*?</span>', "", fragment, flags=re.S)
    fragment = re.sub(r"<br\s*/?>|</div>|</li>|</p>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.splitlines()]
    return "\n".join(l for l in lines if l)


def _section(page: str, prop: str) -> str:
    m = re.search(rf'<section itemprop="{prop}"[^>]*>(.*?)</section>', page, re.S)
    return m.group(1) if m else ""


def _parse_claims(section: str) -> list[dict]:
    blocks = re.split(r'(?=<div[^>]*\bnum="\d+"[^>]*class="claim")', section)
    claims = []
    for b in blocks:
        m = re.match(r'<div[^>]*\bnum="0*(\d+)"', b)
        if not m:
            continue
        text = _clean(b)
        text = re.sub(rf"^{m.group(1)}\s*\.\s*", "", text)  # US 원문의 '1. ' 접두 제거 (번호는 따로 관리)
        if text:
            claims.append({"number": int(m.group(1)), "text": text})
    return claims


def fetch_patent(raw_number: str) -> dict:
    """특허 번호로 Google Patents를 조회해 실제 원문 정보를 반환한다.

    반환: {"number", "url", "title", "abstract", "claims": [{"number", "text"}], "language"}
    """
    cands = candidate_numbers(raw_number)
    if not cands:
        raise PatentLookupError("특허 번호를 입력해 주세요.")
    last_status = None
    for num in cands:
        lang = "ko" if num.startswith("KR") else "en"
        url = f"https://patents.google.com/patent/{num}/{lang}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SEC)
        except requests.RequestException as e:
            raise PatentLookupError(f"Google Patents에 연결하지 못했습니다: {e}") from e
        last_status = resp.status_code
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            raise PatentLookupError(f"Google Patents 조회 오류 (HTTP {resp.status_code})")

        page = resp.content.decode("utf-8", errors="replace")  # 응답 헤더에 charset이 없어 직접 UTF-8 디코딩
        m = re.search(r'<meta name="DC.title" content="([^"]*)"', page)
        title = html.unescape(m.group(1)).strip() if m else ""
        abstract = _clean(_section(page, "abstract")).removeprefix("Abstract").strip()
        claims = _parse_claims(_section(page, "claims"))
        if not title and not claims:
            raise PatentLookupError(f"{num} 페이지에서 특허 정보를 읽지 못했습니다. (페이지 구조 변경 가능성)")
        return {
            "number": num,
            "url": f"https://patents.google.com/patent/{num}/{lang}",
            "title": title,
            "abstract": abstract,
            "claims": claims,
            "language": lang,
        }
    tried = ", ".join(cands)
    raise PatentLookupError(
        f"Google Patents에서 특허를 찾지 못했습니다 (조회한 번호: {tried}, HTTP {last_status}). 번호와 종류 코드(A/B1 등)를 확인해 주세요."
    )


def patent_to_prompt_text(p: dict) -> str:
    """분석 프롬프트에 넣을 참조 특허 텍스트 (실제 원문)."""
    live = [c for c in p["claims"] if c["text"].strip() not in ("삭제", "delete", "(deleted)")]  # 삭제된 청구항 제외
    claims = "\n".join(f"【청구항 {c['number']}】 {c['text']}" for c in live) or "(청구항 정보 없음)"
    return (
        f"특허 번호: {p['number']}\n출처: {p['url']}\n발명의 명칭: {p['title']}\n\n"
        f"[요약]\n{p['abstract'] or '(요약 정보 없음)'}\n\n[청구범위]\n{claims}"
    )
