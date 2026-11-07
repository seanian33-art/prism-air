"""KIPRIS Plus Open API 연동 + 실제 검색된 선행특허 기반 AI 팩트체크.

API 규격 (KIPRIS Plus '특허·실용신안 정보 검색 서비스'):
    GET {BASE_URL}/getWordSearch?word=...&patent=true&utility=true&pageNo=1&numOfRows=10&ServiceKey=...
    응답: XML  response/header/{resultCode,resultMsg}, response/body/items/item/{applicationNumber, inventionTitle,
          astrtCont, applicantName, applicationDate, registerStatus, openNumber, registerNumber, ipcNumber ...}
인증키: KIPRIS Plus(plus.kipris.or.kr) 회원가입 후 해당 서비스 활용 신청 → .env의 KIPRIS_API_KEY

원칙: 팩트체크는 '실제로 검색된 특허'만 근거로 한다. 키가 없거나 검색이 실패하면
결과를 만들어 내지 않고 KiprisError로 이유를 알린다.
"""

import json
import os
import urllib.parse
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

from analyzer import PERSONA, AnalyzerError, _call_claude

load_dotenv()

BASE_URL = "https://plus.kipris.or.kr/kipo-api/kipi/patUtiModInfoSearchSevice"
TIMEOUT_SEC = 20
ROWS_PER_QUERY = 10
MAX_PATENTS = 15  # 팩트체크에 넘길 최대 특허 수 (중복 제거 후)

ITEM_FIELDS = [
    "applicationNumber", "inventionTitle", "astrtCont", "applicantName", "applicationDate",
    "registerStatus", "openNumber", "openDate", "registerNumber", "registerDate", "ipcNumber",
]


class KiprisError(Exception):
    """KIPRIS 검색 실패 (사용자에게 그대로 보여줄 수 있는 메시지)."""


def _service_key() -> str:
    key = os.getenv("KIPRIS_API_KEY", "").strip()
    if not key or key.startswith("YOUR_") or key == "여기에_키_입력":
        raise KiprisError(
            "KIPRIS API 키가 없습니다. KIPRIS Plus(plus.kipris.or.kr)에서 '특허·실용신안 정보 검색' 서비스 "
            "활용 신청 후 받은 인증키를 .env의 KIPRIS_API_KEY에 입력해 주세요."
        )
    return urllib.parse.unquote(key)  # 공공데이터 포털에서 복사한 %인코딩 키도 허용


def search_kipris(word: str, rows: int = ROWS_PER_QUERY) -> list[dict]:
    """KIPRIS Plus getWordSearch로 특허·실용신안을 검색해 item 목록을 반환한다."""
    params = {
        "word": word,
        "patent": "true",
        "utility": "true",
        "pageNo": "1",
        "numOfRows": str(rows),
        "ServiceKey": _service_key(),
    }
    try:
        resp = requests.get(f"{BASE_URL}/getWordSearch", params=params, timeout=TIMEOUT_SEC)
    except requests.RequestException as e:
        raise KiprisError(f"KIPRIS 서버에 연결하지 못했습니다: {e}") from e
    if resp.status_code != 200:
        raise KiprisError(f"KIPRIS 서버 오류 (HTTP {resp.status_code})")

    body = resp.content.lstrip(b"\xef\xbb\xbf").lstrip()
    # 점검·장애 시 KIPRIS는 HTTP 200과 함께 HTML 안내 페이지를 돌려준다 → '검색 결과 0건'으로 오인하지 않도록 구분
    if body[:200].lower().startswith((b"<!doctype html", b"<html")):
        raise KiprisError("KIPRIS Plus 서비스가 현재 이용 불가 상태입니다 (점검 또는 장애 안내 페이지 응답). 잠시 후 다시 시도해 주세요.")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise KiprisError(f"KIPRIS 응답을 해석하지 못했습니다: {e}") from e

    code = (root.findtext(".//header/resultCode") or "").strip()
    if code and code != "00":
        msg = (root.findtext(".//header/resultMsg") or "").strip()
        raise KiprisError(f"KIPRIS API 오류 ({code}): {msg or '알 수 없는 오류'}")

    items = []
    for item in root.iter("item"):
        rec = {f: (item.findtext(f) or "").strip() for f in ITEM_FIELDS}
        if rec["applicationNumber"] or rec["inventionTitle"]:
            items.append(rec)
    return items


# ---------------------------------------------------------------------------
# Claude: 검색어 생성 + 팩트체크
# ---------------------------------------------------------------------------
QUERY_PROMPT = (
    f"{PERSONA} 지금은 한국 특허 DB(KIPRIS)에서 선택된 아이디어의 선행특허를 찾기 위한 검색어를 만드는 단계야. "
    "아이디어의 핵심 구성요소 조합을 반영한 한국어 검색어 3개를 만들어. 각 검색어는 2~4개의 한국어 기술 용어를 "
    "공백으로 이은 형태로 (예: '블랙매트릭스 컬러필터 봉지층'), 너무 넓지도 좁지도 않게. "
    "첫 번째는 가장 핵심 조합, 나머지는 대체 용어나 인접 구성으로."
)
QUERY_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
    "additionalProperties": False,
}

FACT_CHECK_PROMPT = (
    f"{PERSONA} 지금은 KIPRIS에서 실제로 검색된 선행특허 목록과 선택된 아이디어를 대조하는 팩트체크 단계야.\n\n"
    "<rules>\n"
    "- 반드시 <patents>에 주어진 특허만 근거로 사용해. 목록에 없는 특허 번호·출원인·내용을 절대 만들어 내지 마.\n"
    "- 특허를 언급할 때는 주어진 출원번호와 발명의 명칭을 그대로 써.\n"
    "- 주어진 정보는 서지사항과 초록뿐이다. 청구항 전문을 본 것처럼 단정하지 말고, 초록 기준 판단임을 명시해.\n"
    "</rules>\n\n"
    "<task>\n"
    "### 1. 검색 결과 요약\n"
    "- 검색어별 적중 경향, 관련성이 높은 특허 수.\n"
    "### 2. 핵심 선행특허 대조표\n"
    "- 관련성이 높은 특허(최대 5건)에 대해 | 출원번호 | 발명의 명칭 | 출원인 | 중첩 구성요소 | 차이점 | 중첩도(상/중/하) | 표.\n"
    "### 3. 신규성·진보성 리스크\n"
    "- 아이디어의 각 핵심 구성요소가 위 특허들에 개시되어 있는지 판단하고, 리스크 수준과 근거.\n"
    "### 4. 침해·회피 관점 코멘트\n"
    "- 등록 상태(registerStatus)를 고려해 실제 권리 리스크가 있는 특허와 회피 방향.\n"
    "### 5. 결론 및 후속 조치\n"
    "- 종합 판정, 청구항 전문을 확인해야 할 특허 목록, 추가 검색 권장어.\n"
    "</task>\n\n"
    "출력은 한국어 마크다운. 맨 위 문서 제목(#) 없이 '### '부터 시작하고 HTML·코드 블록은 쓰지 마. "
    "마지막 줄에 '※ 본 결과는 KIPRIS 검색 결과(서지·초록)에 기반한 AI 사전 검토이며, 정식 판단은 청구항 전문 검토와 변리사 의견이 필요합니다.'를 적어."
)


def generate_kipris_queries(idea_text: str) -> list[str]:
    text = _call_claude(
        system=QUERY_PROMPT,
        user_message=f"<idea>\n{idea_text}\n</idea>",
        max_tokens=2000,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": QUERY_SCHEMA}},
    )
    try:
        queries = json.loads(text).get("queries", [])
    except json.JSONDecodeError:
        raise AnalyzerError("KIPRIS 검색어 생성 응답을 해석하지 못했습니다.")
    return [q.strip() for q in queries if q.strip()][:3]


def _format_patents(patents: list[dict]) -> str:
    rows = []
    for p in patents:
        rows.append(
            "<patent>\n"
            + "\n".join(f"{k}: {p.get(k, '')}" for k in ITEM_FIELDS if p.get(k))
            + "\n</patent>"
        )
    return "<patents>\n" + "\n".join(rows) + "\n</patents>"


def search_kipris_and_fact_check(keyword: str, idea_text: str) -> dict:
    """KIPRIS에서 선행특허를 실제로 검색하고, 검색된 특허만 근거로 아이디어를 대조한 팩트체크 리포트를 만든다.

    keyword: 사용자가 지정한 추가 검색어 (보통 아이디어 제목). 비어 있어도 된다.
    반환: {"queries": [...], "patents": [...], "report": 마크다운}
    예외: KiprisError (키 없음·서비스 중단·API 오류), AnalyzerError (Claude 호출 오류)
    """
    _service_key()  # 키가 없으면 Claude 호출 전에 바로 알림 (비용 낭비 방지)
    # KIPRIS 점검·장애·인증 오류를 Claude 호출 전에 확인 (1건짜리 가벼운 검색)
    search_kipris(keyword.strip() or "표시 장치", rows=1)
    queries = generate_kipris_queries(f"{keyword}\n{idea_text}" if keyword else idea_text)
    if not queries:
        raise KiprisError("KIPRIS 검색어를 만들지 못했습니다.")

    patents, seen, per_query = [], set(), {}
    for q in queries:
        hits = search_kipris(q)
        per_query[q] = len(hits)
        for h in hits:
            key = h["applicationNumber"] or h["inventionTitle"]
            if key not in seen:
                seen.add(key)
                h["matched_query"] = q
                patents.append(h)
    patents = patents[:MAX_PATENTS]

    if not patents:
        report = (
            "### KIPRIS 검색 결과 없음\n"
            f"- 검색어: {', '.join(queries)}\n"
            "- 위 검색어로는 KIPRIS에서 선행특허가 검색되지 않았습니다. 검색어 범위가 좁았을 수 있으며, "
            "검색 결과가 없다는 것이 신규성을 보장하지는 않습니다. 영문 DB(Google Patents 등) 검색을 병행해 주세요."
        )
        return {"queries": queries, "per_query": per_query, "patents": [], "report": report}

    report = _call_claude(
        system=FACT_CHECK_PROMPT,
        user_message=(
            f"<idea>\n{idea_text}\n</idea>\n\n"
            f"<search_queries>\n" + "\n".join(f"- {q}: {per_query[q]}건" for q in queries) + "\n</search_queries>\n\n"
            + _format_patents(patents)
        ),
        max_tokens=12000,
        output_config={"effort": "medium"},
    ).strip()
    return {"queries": queries, "per_query": per_query, "patents": patents, "report": report}
