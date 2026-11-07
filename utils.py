"""유틸리티 함수 모음."""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlencode

import plotly.graph_objects as go
import pypdf

from analyzer import DEEP_ANALYSES, SCORE_METRICS, AnalyzerError

DIAGRAM_MAX_WIDTH = 900  # 단면도 최대 표시 폭(px)
HISTORY_PATH = Path(__file__).parent / "history.json"

# 차트 색상 (기준 = 파랑, 시뮬레이션 = 주황)
COLOR_BASE = "#2a78d6"
COLOR_SIM = "#eb6834"


def extract_text_from_pdf(pdf_file) -> str:
    """구글 특허 등에서 다운로드한 PDF 파일 객체로부터 텍스트를 추출한다.

    실패하면 AnalyzerError를 발생시킨다. (오류 문구를 '추출 텍스트'로 반환하면
    그 문구가 특허 본문인 것처럼 분석될 수 있으므로 반환하지 않는다.)
    """
    return parse_patent_pdf(pdf_file)["text"]


# 청구범위 시작을 알리는 표지 (Google Patents 영문/국문 PDF 공통 패턴)
_CLAIMS_MARKERS = re.compile(
    r"(what is claimed is|we claim|i claim|claims\s*:|【\s*청구범위\s*】|청구범위|특허청구의 범위|請求の範囲)",
    re.IGNORECASE,
)


def parse_patent_pdf(pdf_file) -> dict:
    """PDF에서 텍스트를 추출하고 페이지 수·청구범위 포함 여부를 함께 반환한다.

    반환: {"text": str, "pages": int, "has_claims": bool}
    """
    try:
        if hasattr(pdf_file, "seek"):
            pdf_file.seek(0)
        reader = pypdf.PdfReader(pdf_file)
        if reader.is_encrypted:
            reader.decrypt("")  # 열람 암호 없이 사용 제한만 걸린 PDF 처리
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:
        raise AnalyzerError(f"PDF 텍스트 추출 중 오류가 발생했습니다: {e}") from e

    text = "\n".join(p for p in pages if p.strip()).strip()
    if not text:
        raise AnalyzerError("PDF에서 텍스트를 추출하지 못했습니다. (스캔 이미지로만 된 PDF일 수 있습니다)")
    return {"text": text, "pages": len(pages), "has_claims": bool(_CLAIMS_MARKERS.search(text))}


# ---------------------------------------------------------------------------
# 결과 → 마크다운
# ---------------------------------------------------------------------------
def prior_art_to_markdown(result: dict) -> str:
    prior = result.get("prior_art", {})
    lines = ["### 📌 기술 요약", prior.get("summary", ""), "", "### ⚠️ 선행기술 취약점"]
    for i, w in enumerate(prior.get("weaknesses", []), 1):
        lines.append(f"**{i}. {w['title']}**  \n{w['description']}\n")
    return "\n".join(lines)


def trl_badge(trl: int) -> str:
    """TRL 값을 Streamlit 마크다운 배지 문법으로 변환한다."""
    if trl >= 7:
        color, stage = "green", "양산 적용 단계"
    elif trl >= 4:
        color, stage = "orange", "개발·검증 단계"
    else:
        color, stage = "red", "기초 연구 단계"
    return f":{color}-badge[:material/science: TRL {trl} · {stage}]"


def idea_to_markdown(i: int, idea: dict) -> str:
    """아이디어 카드 본문 (제목·성격 태그는 theme.render_card_title이 별도로 그린다)."""
    principles = ", ".join(idea.get("triz_principles", [])) or "-"
    relation = idea.get("history_relation", "").replace("[", "(").replace("]", ")")
    return "\n".join(
        [
            f"{trl_badge(idea.get('trl', 1))} :gray-badge[:material/history: {relation[:40] or '신규'}]",
            "",
            f"_TRL 판정 근거: {idea.get('trl_rationale', '-')}_",
            "",
            f"- **기술적 모순:** {idea['contradiction']}",
            f"- **적용 TRIZ 원리:** {principles}",
            "",
            idea["description"],
            "",
            f"> **기대 효과:** {idea['expected_effect']}",
        ]
    )


def idea_to_diagram_text(idea: dict, result: dict) -> str:
    """단면도 생성 요청(generate_svg_diagram)에 넘길 아이디어 설명 텍스트를 만든다."""
    layers = ", ".join(f"{c['layer']} {c['percent']:.0f}%" for c in idea.get("layer_contributions", []))
    return "\n".join(
        [
            f"아이디어 제목: {idea['title']}",
            f"선행기술 요약: {result.get('prior_art', {}).get('summary', '')}",
            f"기술적 모순: {idea['contradiction']}",
            f"적용 TRIZ 원리: {', '.join(idea.get('triz_principles', []))}",
            f"상세 설명: {idea['description']}",
            f"기대 효과: {idea['expected_effect']}",
            f"레이어별 개선 기여도: {layers or '-'}",
        ]
    )


def claims_to_markdown(claims: list[dict]) -> str:
    lines = []
    for c in sorted(claims, key=lambda c: c["number"]):
        lines += [f"#### 【청구항 {c['number']}】 ({c['claim_type']})", c["text"], ""]
    return "\n".join(lines) or "_작성된 청구항이 없습니다._"


def claims_to_text(claims: list[dict]) -> str:
    """Word 리포트용 청구항 평문 ('【청구항 N】' 줄 + 본문)."""
    lines = []
    for c in sorted(claims, key=lambda c: c["number"]):
        lines += [f"【청구항 {c['number']}】 ({c['claim_type']})", c["text"], ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 분석 이력 (history.json)
# ---------------------------------------------------------------------------
def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        # 손상된 파일은 덮어쓰지 않도록 백업해 둔다.
        backup = HISTORY_PATH.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json")
        HISTORY_PATH.replace(backup)
        return []


def _fallback_title(text: str, limit: int = 20) -> str:
    """title이 없을 때 입력 요약의 앞부분으로 제목을 만든다."""
    head = re.split(r"[|,/\n]", text or "", maxsplit=1)[0].strip()
    if not head:
        return "제목 없는 분석"
    return head if len(head) <= limit else head[:limit].rstrip() + "…"


def history_label(entry: dict) -> str:
    """사이드바 표시용: '제목 (YYYY-MM-DD HH:MM)'. title이 없는 과거 이력도 처리한다."""
    title = entry.get("title") or _fallback_title(entry.get("keywords", ""))
    return f"{title} ({entry.get('date', '')})"


def _save_history(history: list[dict]) -> None:
    tmp = HISTORY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HISTORY_PATH)


def append_history(mode_label: str, input_summary: str, result: dict) -> str:
    """분석 결과를 history.json에 누적 저장하고, 새 이력의 id를 반환한다.

    요약 필드(title/keywords/ideas)는 사이드바 표시와 다음 분석의 컨텍스트용이고,
    full_result는 화면 복원용 전체 결과 데이터다. (컨텍스트로는 요약만 모델에 전달됨)
    """
    history = load_history()
    keywords = result.get("prior_art", {}).get("search_keywords", [])
    entry_id = uuid.uuid4().hex[:12]
    history.append(
        {
            "id": entry_id,
            "title": (result.get("title") or "").strip() or _fallback_title(input_summary),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mode": mode_label,
            "keywords": input_summary[:200],
            "search_keywords": keywords,
            "ideas": [
                {
                    "title": idea.get("title", ""),
                    "trl": idea.get("trl"),
                    "summary": idea.get("expected_effect", "")[:150],
                }
                for idea in result.get("ideas", [])
            ],
            "full_result": result,
            "diagrams": {},  # 아이디어 번호(문자열) → 온디맨드로 생성한 SVG
        }
    )
    _save_history(history)
    return entry_id


def save_history_diagram(entry_id: str, idea_no: int, svg: str) -> None:
    """온디맨드로 생성한 구조도를 해당 이력에 저장한다 (불러오기 시 API 재호출 없이 복원)."""
    _update_history_entry(entry_id, "diagrams", idea_no, svg)


def save_history_maturation(entry_id: str, idea_no: int, data: dict) -> None:
    """온디맨드로 생성한 청구항+도면(고도화 결과)을 해당 이력에 저장한다."""
    _update_history_entry(entry_id, "maturations", idea_no, data)


def save_history_kipris(entry_id: str, idea_no: int, data: dict) -> None:
    """KIPRIS 팩트체크 결과(검색어·검색 특허·리포트)를 해당 이력에 저장한다."""
    _update_history_entry(entry_id, "kipris", idea_no, data)


def save_history_deep_analysis(entry_id: str, idea_no: int, kind: str, text: str) -> None:
    """심층 분석 결과를 이력에 저장한다: deep_analyses[아이디어 번호][kind] = 마크다운."""
    history = load_history()
    for entry in history:
        if entry.get("id") == entry_id:
            entry.setdefault("deep_analyses", {}).setdefault(str(idea_no), {})[kind] = text
            _save_history(history)
            return


def _update_history_entry(entry_id: str, field: str, idea_no: int, value) -> None:
    history = load_history()
    for entry in history:
        if entry.get("id") == entry_id:
            entry.setdefault(field, {})[str(idea_no)] = value
            _save_history(history)
            return


# ---------------------------------------------------------------------------
# 아이디어 보관함 (saved_ideas.json)
# ---------------------------------------------------------------------------
SAVED_IDEAS_PATH = Path(__file__).parent / "saved_ideas.json"


def load_saved_ideas() -> list[dict]:
    if not SAVED_IDEAS_PATH.exists():
        return []
    try:
        data = json.loads(SAVED_IDEAS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        backup = SAVED_IDEAS_PATH.with_suffix(f".broken-{datetime.now():%Y%m%d%H%M%S}.json")
        SAVED_IDEAS_PATH.replace(backup)
        return []


def saved_item_id(entry: dict) -> str:
    """보관함 항목의 고유 id (id가 없던 초기 저장분은 출처 분석 id + 아이디어 번호로 대체)."""
    return entry.get("item_id") or f"{entry.get('source_id', 'x')}-{entry.get('idea_no', 0)}"


def write_saved_ideas(saved: list[dict]) -> None:
    """보관함 전체 목록을 saved_ideas.json에 원자적으로 기록한다 (임시 파일 → 교체)."""
    tmp = SAVED_IDEAS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SAVED_IDEAS_PATH)


def save_ideas(entries: list[dict]) -> int:
    """아이디어를 saved_ideas.json에 저장한다. 같은 분석의 같은 아이디어는 최신 내용으로 덮어쓴다.

    entries: [{"source_id", "idea_no", "analysis_title", "idea", "diagram_svg", "maturation"}]
    반환: 저장 후 전체 보관 개수
    """
    saved = load_saved_ideas()
    index = {(s.get("source_id"), s.get("idea_no")): i for i, s in enumerate(saved)}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for e in entries:
        key = (e.get("source_id"), e.get("idea_no"))
        if key in index:  # 덮어쓰기: 기존 id 유지
            prev = saved[index[key]]
            saved[index[key]] = {**e, "item_id": saved_item_id(prev), "saved_at": now}
        else:
            index[key] = len(saved)
            saved.append({**e, "item_id": uuid.uuid4().hex[:12], "saved_at": now})
    write_saved_ideas(saved)
    return len(saved)


def delete_saved_idea(item_id: str) -> bool:
    """보관함에서 해당 id의 아이디어를 삭제한다. 삭제했으면 True."""
    saved = load_saved_ideas()
    remaining = [s for s in saved if saved_item_id(s) != item_id]
    if len(remaining) == len(saved):
        return False
    write_saved_ideas(remaining)
    return True


def generate_saved_ideas_docx(saved: list[dict]) -> bytes:
    """보관함의 모든 아이디어를 하나의 Word 보고서로 만든다.

    아이디어마다: 메타 정보, 요약, 설명, 8대 지표(방사형 차트 + 표), 레이어 기여도,
    (있으면) 구조도와 청구항 초안.
    """
    import io

    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    from analyzer import _svg_to_png, add_report_heading, new_report_doc

    doc = new_report_doc("아이디어 보관함 보고서", subtitle=f"총 {len(saved)}건")

    # 목차 요약 표
    add_report_heading(doc, "보관 아이디어 목록")
    table = doc.add_table(rows=1, cols=4)
    table.style = "Light Grid Accent 1"
    for cell, text in zip(table.rows[0].cells, ["No.", "아이디어", "TRL", "저장 일시"]):
        cell.text = text
    for n, s in enumerate(saved, 1):
        idea = s.get("idea", {})
        cells = table.add_row().cells
        cells[0].text = str(n)
        cells[1].text = idea.get("title", "")
        cells[2].text = str(idea.get("trl", "-"))
        cells[3].text = s.get("saved_at", "")

    for n, s in enumerate(saved, 1):
        idea = s.get("idea", {})
        doc.add_page_break()
        add_report_heading(doc, f"{n}. {idea.get('title', '')}")
        meta = doc.add_paragraph()
        tag = f"{idea['category_tag']}  |  " if idea.get("category_tag") else ""
        run = meta.add_run(
            f"{tag}출처 분석: {s.get('analysis_title') or '-'}  |  TRL {idea.get('trl', '-')}  |  저장 일시: {s.get('saved_at', '')}"
        )
        run.font.size = Pt(9)

        for label, value in [
            ("기대 효과", idea.get("expected_effect")),
            ("기술적 모순", idea.get("contradiction")),
            ("적용 TRIZ 원리", ", ".join(idea.get("triz_principles", []))),
            ("상세 설명", idea.get("description")),
            ("TRL 판정 근거", idea.get("trl_rationale")),
        ]:
            if value:
                p = doc.add_paragraph()
                p.add_run(f"{label}: ").bold = True
                p.add_run(str(value))

        scores = idea.get("scores")
        if scores:
            add_report_heading(doc, "8대 지표 정량 평가", level=2)
            png = _svg_to_png(radar_svg(scores), width_px=1400)
            if png:
                doc.add_picture(io.BytesIO(png), width=Cm(13))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
            st_table = doc.add_table(rows=2, cols=len(SCORE_METRICS))
            st_table.style = "Light Grid Accent 1"
            for i, (key, label) in enumerate(SCORE_METRICS):
                st_table.rows[0].cells[i].text = label
                st_table.rows[1].cells[i].text = str(scores.get(key, "-"))

        contribs = idea.get("layer_contributions") or []
        if contribs:
            add_report_heading(doc, "레이어별 개선 기여도", level=2)
            doc.add_paragraph(", ".join(f"{c['layer']} {c['percent']:.0f}%" for c in contribs))

        maturation = s.get("maturation") or {}
        svg = sanitize_svg(s.get("diagram_svg") or maturation.get("svg") or "")
        if svg:
            add_report_heading(doc, "스택 구조 단면도", level=2)
            png = _svg_to_png(svg)
            if png:
                doc.add_picture(io.BytesIO(png), width=Cm(16))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER

        if maturation.get("claims"):
            add_report_heading(doc, "청구항 초안", level=2)
            for c in maturation["claims"]:
                p = doc.add_paragraph()
                p.add_run(f"【청구항 {c['number']}】 ({c['claim_type']}) ").bold = True
                p.add_run(c["text"])

        for kind, text in (s.get("deep_analyses") or {}).items():
            if kind in DEEP_ANALYSES and text:
                add_report_heading(doc, DEEP_ANALYSES[kind][0], level=2)
                markdown_to_docx(doc, text)

        kres = s.get("kipris") or {}
        if kres.get("report"):
            add_report_heading(doc, "🏛️ KIPRIS 선행특허 팩트체크", level=2)
            doc.add_paragraph("검색어: " + ", ".join(kres.get("queries", [])))
            if kres.get("patents"):
                kt = doc.add_table(rows=1, cols=4)
                kt.style = "Light Grid Accent 1"
                for cell, text in zip(kt.rows[0].cells, ["출원번호", "발명의 명칭", "출원인", "상태"]):
                    cell.text = text
                for p in kres["patents"]:
                    cells = kt.add_row().cells
                    for cell, f in zip(cells, ["applicationNumber", "inventionTitle", "applicantName", "registerStatus"]):
                        cell.text = p.get(f, "")
            markdown_to_docx(doc, kres["report"])

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _add_inline_markdown(paragraph, text: str) -> None:
    """'**굵게**' 구간만 굵게 처리해 문단에 추가한다."""
    for k, part in enumerate(re.split(r"\*\*", text)):
        if part:
            paragraph.add_run(part).bold = k % 2 == 1


def markdown_to_docx(doc, md: str) -> None:
    """심층 분석 마크다운(소제목, 목록, 표, 굵게)을 Word 서식으로 옮긴다."""
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|"):  # 표 블록
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):  # 구분선 행 제외
                    rows.append(cells)
                i += 1
            if rows:
                ncols = max(len(r) for r in rows)
                table = doc.add_table(rows=len(rows), cols=ncols)
                table.style = "Light Grid Accent 1"
                for r, row in enumerate(rows):
                    for c, cell in enumerate(row):
                        table.cell(r, c).text = cell.replace("**", "")
            continue
        if line.startswith("#"):
            doc.add_heading(line.lstrip("#").strip(), level=3)
        elif re.match(r"^[-*] ", line):
            _add_inline_markdown(doc.add_paragraph(style="List Bullet"), line[2:])
        elif re.match(r"^\d+\. ", line):
            _add_inline_markdown(doc.add_paragraph(style="List Number"), re.sub(r"^\d+\. ", "", line))
        elif line:
            _add_inline_markdown(doc.add_paragraph(), line)
        i += 1


def radar_svg(scores: dict, size: int = 520) -> str:
    """Word 리포트용 8각형 방사형 차트 SVG (Plotly 이미지 변환 없이 resvg로 래스터화)."""
    import math

    width = int(size * 1.5)  # 좌우 축 라벨('재료 적합성', '특허 회피성')이 잘리지 않도록 가로 여백 확보
    cx, cy = width / 2, size / 2
    r_max = size * 0.32
    n = len(SCORE_METRICS)

    def point(i, value):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        r = r_max * value / 10
        return cx + r * math.cos(ang), cy + r * math.sin(ang)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {size}">',
             f'<rect width="{width}" height="{size}" fill="#ffffff"/>']
    for level in (2, 4, 6, 8, 10):  # 격자
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, level) for i in range(n)))
        parts.append(f'<polygon points="{pts}" fill="none" stroke="#d0d0d0" stroke-width="1"/>')
    for i, (key, label) in enumerate(SCORE_METRICS):  # 축 + 라벨
        x, y = point(i, 10)
        lx, ly = point(i, 12.6)
        anchor = "middle" if abs(lx - cx) < 5 else ("start" if lx > cx else "end")
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e0e0e0"/>')
        parts.append(
            f'<text x="{lx:.1f}" y="{ly + 5:.1f}" text-anchor="{anchor}" font-size="15" '
            f'font-family="Malgun Gothic" fill="#333">{label} {scores.get(key, 0)}</text>'
        )
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, scores.get(k, 0)) for i, (k, _) in enumerate(SCORE_METRICS)))
    parts.append(f'<polygon points="{pts}" fill="{COLOR_BASE}" fill-opacity="0.18" stroke="{COLOR_BASE}" stroke-width="2.5"/>')
    for i, (k, _) in enumerate(SCORE_METRICS):
        x, y = point(i, scores.get(k, 0))
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{COLOR_BASE}" stroke="#fff" stroke-width="2"/>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 레퍼런스 검색 URL (LLM이 아닌 코드에서 조립 → URL 환각 방지)
# ---------------------------------------------------------------------------
def scholar_url(keywords: list[str]) -> str:
    query = "OLED " + " ".join(f'"{k}"' for k in keywords[:3])
    return f"https://scholar.google.com/scholar?q={quote_plus(query)}"


def patents_url(keywords: list[str]) -> str:
    # Google Patents는 q 파라미터를 여러 개 주면 AND 조건으로 검색한다.
    params = [("q", f"({k})") for k in ["OLED", *keywords[:3]]]
    return "https://patents.google.com/?" + urlencode(params)


# ---------------------------------------------------------------------------
# What-If 파라미터 시뮬레이터
# ---------------------------------------------------------------------------
# effects: 기준값 대비 파라미터 1단위 변화당 지표 점수 변화량.
# ※ 현재는 일반적인 경향을 반영한 가정값(뼈대)이다. 실측 데이터나 광학 시뮬레이션 결과로 교체할 것.
SIM_PARAMS = [
    {
        "key": "bm_od",
        "label": "BM 광학 농도 (OD)",
        "min": 1.0, "max": 5.0, "baseline": 3.0, "step": 0.1, "fmt": "%.1f",
        "effects": {"reflection_reduction": 0.9, "outcoupling": -0.3,
                    "manufacturability": -0.4, "cost": -0.3},
    },
    {
        "key": "cf_thickness",
        "label": "CF 두께 (µm)",
        "min": 0.5, "max": 4.0, "baseline": 2.0, "step": 0.1, "fmt": "%.1f",
        "effects": {"reflection_reduction": 0.7, "outcoupling": -1.0,
                    "material_fit": -0.2, "manufacturability": -0.3, "cost": -0.3},
    },
    {
        "key": "tfe_thickness",
        "label": "TFE 총 두께 (µm)",
        "min": 2.0, "max": 15.0, "baseline": 8.0, "step": 0.5, "fmt": "%.1f",
        "effects": {"drive_reliability": 0.25, "outcoupling": -0.08,
                    "manufacturability": -0.1, "cost": -0.1},
    },
]


def weighted_score(scores: dict, priority_keys: list[str]) -> float:
    """8개 지표 가중 평균 (10점 만점). priority_keys 지표는 2배 가중치."""
    weights = {k: (2.0 if k in priority_keys else 1.0) for k, _ in SCORE_METRICS}
    return sum(scores.get(k, 0) * w for k, w in weights.items()) / sum(weights.values())


def settings_summary(settings: dict) -> str:
    """분석 조건을 한 줄 요약으로 표시한다."""
    return (
        f"창의성 {settings.get('temperature', 0.5):.1f} · "
        f"{settings.get('priority_metric', '-')} · {settings.get('target_trl', '-')}"
    )


def simulate_scores(base: dict, values: dict) -> dict:
    """슬라이더 값에 따라 8개 지표 점수를 재계산한다 (1~10 범위로 제한)."""
    sim = {k: float(base.get(k, 0)) for k, _ in SCORE_METRICS}
    for p in SIM_PARAMS:
        delta = values.get(p["key"], p["baseline"]) - p["baseline"]
        for metric, coef in p["effects"].items():
            sim[metric] += coef * delta
    return {k: round(max(1.0, min(10.0, v)), 1) for k, v in sim.items()}


# ---------------------------------------------------------------------------
# Plotly 차트
# ---------------------------------------------------------------------------
def radar_chart(base: dict, sim: dict | None = None, height: int = 440) -> go.Figure:
    """8각형 방사형 차트 (최대축 10). sim이 있으면 기준 대비 겹쳐 그린다."""
    labels = [label for _, label in SCORE_METRICS]
    keys = [k for k, _ in SCORE_METRICS]

    def trace(scores, name, color, fill_alpha):
        r = [scores[k] for k in keys]
        return go.Scatterpolar(
            r=r + r[:1],
            theta=labels + labels[:1],
            name=name,
            fill="toself",
            fillcolor=f"rgba({int(color[1:3], 16)},{int(color[3:5], 16)},{int(color[5:7], 16)},{fill_alpha})",
            line=dict(color=color, width=2),
            marker=dict(size=8, color=color),
            hovertemplate="%{theta}: <b>%{r}</b> / 10<extra>" + name + "</extra>",
        )

    fig = go.Figure(trace(base, "AI 평가 (기준)", COLOR_BASE, 0.15))
    if sim is not None:
        fig.add_trace(trace(sim, "What-If 시뮬레이션", COLOR_SIM, 0.10))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(range=[0, 10], dtick=2, showline=False, gridcolor="rgba(128,128,128,0.25)"),
            angularaxis=dict(gridcolor="rgba(128,128,128,0.25)"),
        ),
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="center", x=0.5),
        margin=dict(l=60, r=60, t=30, b=40),
        height=height,
    )
    return fig


def layer_contribution_chart(contributions: list[dict]) -> go.Figure:
    """레이어별 개선 기여도(%) 가로 막대그래프."""
    rows = sorted(contributions, key=lambda c: c["percent"])
    fig = go.Figure(
        go.Bar(
            x=[c["percent"] for c in rows],
            y=[c["layer"] for c in rows],
            orientation="h",
            marker=dict(color=COLOR_BASE, cornerradius=4),
            texttemplate="%{x:.0f}%",
            textposition="outside",
            hovertemplate="%{y}: <b>%{x:.1f}%</b><extra></extra>",
        )
    )
    fig.update_layout(
        xaxis=dict(title="개선 기여도 (%)", range=[0, max([c["percent"] for c in rows] + [10]) * 1.18],
                   gridcolor="rgba(128,128,128,0.2)"),
        yaxis=dict(title=None),
        margin=dict(l=10, r=20, t=20, b=40),
        height=max(220, 48 * len(rows) + 80),
        showlegend=False,
    )
    return fig


# ---------------------------------------------------------------------------
# SVG 단면도 처리
# ---------------------------------------------------------------------------
_SVG_RE = re.compile(r"<svg\b.*</svg>", re.IGNORECASE | re.DOTALL)
_REMOVE_RE = [
    re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<foreignObject\b.*?</foreignObject\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE),  # onclick 등
]
_JS_HREF_RE = re.compile(r"(href\s*=\s*[\"'])\s*javascript:[^\"']*", re.IGNORECASE)


def sanitize_svg(raw: str) -> str | None:
    """모델이 생성한 문자열에서 <svg> 요소만 추출하고 스크립트성 요소를 제거한다."""
    if not raw:
        return None
    m = _SVG_RE.search(raw)
    if not m:
        return None
    svg = m.group(0)
    for pattern in _REMOVE_RE:
        svg = pattern.sub("", svg)
    return _JS_HREF_RE.sub(r"\1#", svg)
