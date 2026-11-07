"""PRISM Air - Problem-driven R&D Idea Scouting & Maturation.

디스플레이 R&D 문제 기반 아이디어 발굴 및 특허 후보 도출 자동화 Tool (수석 엔지니어용 의사결정 지원 시스템).
"""

import json
import re
import uuid
from datetime import datetime

import streamlit as st

from analyzer import (
    DEEP_ANALYSES,
    PRIORITY_OPTIONS,
    SCORE_METRICS,
    TRL_OPTIONS,
    AnalyzerError,
    analyze_patent_data,
    generate_claims_and_diagram,
    generate_design_around_analysis,
    generate_docx_report,
    generate_fab_margin_analysis,
    generate_svg_diagram,
    generate_triz_analysis,
)

from google_patents import PatentLookupError, fetch_patent, patent_to_prompt_text
from kipris import KiprisError, search_kipris_and_fact_check
from theme import inject_css, render_card_title, render_header

# 심층 분석 종류 → 호출 함수
DEEP_ANALYSIS_FUNCS = {
    "design_around": generate_design_around_analysis,
    "triz": generate_triz_analysis,
    "fab_margin": generate_fab_margin_analysis,
}
from utils import (
    DIAGRAM_MAX_WIDTH,
    SIM_PARAMS,
    append_history,
    claims_to_markdown,
    claims_to_text,
    delete_saved_idea,
    export_backup_json,
    generate_saved_ideas_docx,
    history_label,
    idea_to_diagram_text,
    idea_to_markdown,
    import_backup,
    layer_contribution_chart,
    load_history,
    load_saved_ideas,
    parse_patent_pdf,
    patents_url,
    prior_art_to_markdown,
    radar_chart,
    sanitize_svg,
    save_history_deep_analysis,
    save_history_diagram,
    save_history_kipris,
    save_history_maturation,
    save_ideas,
    saved_item_id,
    scholar_url,
    settings_summary,
    simulate_scores,
    trl_badge,
    weighted_score,
)

st.set_page_config(
    page_title="PRISM Air",
    page_icon="🌬️",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()  # 하이엔드 엔터프라이즈 SaaS 스타일 (theme.py)

MODES = {
    "1. 키워드/스택 입력": "keyword",
    "2. 단일 특허 문서 업로드": "single",
    "3. 두 특허 비교 융합": "compare",
}

history = load_history()

# ---------------------------------------------------------------------------
# 사이드바: 분석 모드 선택 + 분석 히스토리
# ---------------------------------------------------------------------------
# 각 섹션은 key="sidebar_card_..." 컨테이너로 감싸고, theme.py의 .sidebar-card 스타일을
# Streamlit이 붙여 주는 클래스(st-key-sidebar_card_...)에 적용해 카드 블록으로 보이게 한다.
with st.sidebar:
    st.markdown('<p class="sidebar-brand">🌬️ PRISM Air Dashboard</p>', unsafe_allow_html=True)

    # --- 카드 1: 분석 모드 ---
    with st.container(key="sidebar_card_mode"):
        st.markdown('<p class="sidebar-card-title">⚙️ 설정</p>', unsafe_allow_html=True)
        mode_label = st.radio("분석 모드 선택", list(MODES))

    # --- 카드 2: AI 분석 엔진 ---
    with st.container(key="sidebar_card_engine"):
        st.markdown('<p class="sidebar-card-title">🧠 AI 분석 엔진 세부 설정</p>', unsafe_allow_html=True)
        temperature = st.slider(
            "아이디어 창의성 (Temperature)",
            0.0,
            1.0,
            0.5,
            0.1,
            help="값이 낮을수록 보수적/현실적, 높을수록 창의적/혁신적인 아이디어가 도출됩니다.",
        )
        priority_metric = st.selectbox("최우선 고려 지표", list(PRIORITY_OPTIONS))
        target_trl = st.radio("목표 기술성숙도 (TRL)", list(TRL_OPTIONS))
        use_history = st.toggle(
            "과거 이력 기반 진화형 아이디에이션",
            value=True,
            help="켜면 history.json의 과거 아이디어를 회피·융합 발전시킨 새 아이디어를 요청합니다.",
        )

    # --- 카드 3: 분석 히스토리 ---
    with st.container(key="sidebar_card_history"):
        st.markdown(
            f'<p class="sidebar-card-title">🕘 분석 히스토리 ({len(history)})</p>', unsafe_allow_html=True
        )
        if not history:
            st.caption("아직 분석 이력이 없습니다.")
        for idx, h in reversed(list(enumerate(history))):
            entry_id = h.get("id") or f"legacy{idx}"  # id가 없는 과거 이력은 순번으로 구분
            is_current = st.session_state.get("result_id") == entry_id
            with st.expander(("▶ " if is_current else "") + history_label(h)):
                st.caption(h.get("mode", ""))
                hist_settings = (h.get("full_result") or {}).get("settings")
                if hist_settings:
                    st.caption(settings_summary(hist_settings))
                st.markdown(f"**입력:** {h.get('keywords', '')}")
                for idea in h.get("ideas", []):
                    st.markdown(f"- {idea.get('title', '')} `TRL {idea.get('trl', '?')}`")

                if not h.get("full_result"):
                    st.caption("⚠️ 이 이력은 요약만 저장되어 있어 화면 복원이 불가능합니다. (full_result 저장 이전 분석)")
                elif is_current:
                    st.caption("✅ 현재 화면에 표시 중")
                elif st.button("📂 이 분석 결과 화면에 띄우기", key=f"load_hist_{entry_id}", width="stretch"):
                    st.session_state["result"] = h["full_result"]
                    st.session_state["result_mode"] = h.get("mode", "")
                    st.session_state["result_id"] = entry_id
                    # 저장돼 있던 구조도·청구항 고도화 결과도 함께 복원 (API 재호출 없음)
                    st.session_state["diagrams"] = {
                        f"{entry_id}-{no}": svg for no, svg in h.get("diagrams", {}).items()
                    }
                    st.session_state["maturations"] = {
                        f"{entry_id}-{no}": m for no, m in h.get("maturations", {}).items()
                    }
                    st.session_state["deep_analyses"] = {
                        f"{entry_id}-{no}": d for no, d in h.get("deep_analyses", {}).items()
                    }
                    st.session_state["kipris"] = {f"{entry_id}-{no}": k for no, k in h.get("kipris", {}).items()}
                    st.rerun()

    # --- 카드 4: 이력 백업 / 복원 ---
    # 웹 배포 서버는 재시작·재배포 때 디스크(history.json)가 초기화되므로, 파일로 내려받아 옮겨 담는다.
    with st.container(key="sidebar_card_backup"):
        st.markdown('<p class="sidebar-card-title">💾 이력 백업 / 복원</p>', unsafe_allow_html=True)
        st.caption("분석 히스토리와 아이디어 보관함을 파일 하나로 내보내고, 다른 PC나 웹에서 불러옵니다.")
        st.download_button(
            "⬇️ 이력 내보내기 (JSON)",
            data=export_backup_json(),
            file_name=f"prism_air_backup_{datetime.now():%Y%m%d_%H%M}.json",
            mime="application/json",
            width="stretch",
            key="backup_download",
        )
        backup_file = st.file_uploader(
            "백업 파일 가져오기",
            type=["json"],
            key="backup_upload",
            help="내보낸 백업 파일(또는 history.json / saved_ideas.json)을 올리면 현재 이력에 합쳐집니다. "
            "이미 있는 항목은 건너뛰므로 기존 이력이 지워지지 않습니다.",
        )
        if backup_file is not None and st.button("⬆️ 가져와서 합치기", key="backup_import_btn", width="stretch"):
            try:
                added_h, added_s = import_backup(backup_file.getvalue())
            except ValueError as e:
                st.error(str(e))
            else:
                st.session_state["backup_notice"] = (
                    f"이력 {added_h}건, 보관함 아이디어 {added_s}건을 가져왔습니다."
                    if added_h or added_s
                    else "새로 추가할 항목이 없습니다. (모두 이미 있는 이력입니다)"
                )
                st.rerun()
        if notice := st.session_state.pop("backup_notice", None):
            st.success(notice)
mode = MODES[mode_label]

def render_stack_diagram(raw_svg: str) -> None:
    """모델이 생성한 SVG 단면도를 이미지로 렌더링한다.

    SVG를 <img>로 표시하면 브라우저가 스크립트·외부 리소스를 원천 차단하고,
    다이어그램끼리 <marker> id가 충돌하지 않으며, 앱 테마 CSS의 영향도 받지 않는다.
    (st.markdown(unsafe_allow_html=True)나 iframe에 LLM 출력을 그대로 넣는 방식보다 안전)
    """
    svg = sanitize_svg(raw_svg)
    if not svg:
        st.warning("이 아이디어의 스택 단면도를 생성하지 못했습니다.")
        return
    st.image(svg, width=DIAGRAM_MAX_WIDTH)
    with st.expander("SVG 원본 코드 보기"):
        st.code(svg, language="xml")


# ---------------------------------------------------------------------------
# 메인 화면: 모드별 입력
# ---------------------------------------------------------------------------
def pdf_upload_block(label: str, key: str, help: str | None = None):
    """특허 PDF 업로드 + 즉시 파싱 + 추출 텍스트 미리보기. 반환: (업로드 파일, 파싱 결과 dict 또는 None)

    같은 파일은 다시 파싱하지 않도록 file_id 기준으로 세션에 캐시한다.
    """
    file = st.file_uploader(label, type=["pdf"], key=key, help=help)
    if file is None:
        return None, None

    cache = st.session_state.setdefault("pdf_cache", {})
    cached = cache.get(key)
    if not cached or cached["file_id"] != file.file_id:
        with st.spinner("PDF 문서에서 특허 텍스트와 청구항을 추출하는 중입니다..."):
            try:
                cached = {"file_id": file.file_id, "parsed": parse_patent_pdf(file), "error": None}
            except AnalyzerError as e:
                cached = {"file_id": file.file_id, "parsed": None, "error": str(e)}
        cache[key] = cached

    if cached["error"]:
        st.error(cached["error"])
        return file, None

    parsed = cached["parsed"]
    claims_note = "청구범위 감지됨" if parsed["has_claims"] else "⚠️ 청구범위 표지를 찾지 못함"
    st.success(
        f"PDF 파싱 완료! (총 추출 글자 수: {len(parsed['text']):,}자 · {parsed['pages']}페이지 · {claims_note})"
    )
    with st.expander("추출된 원본 텍스트 미리보기"):
        st.text_area("Parsed Text", parsed["text"], height=200, disabled=True, key=f"{key}_preview")
    return file, parsed


def patent_lookup_block(raw_number: str) -> dict | None:
    """특허 번호 → Google Patents 실제 원문 조회 + 미리보기. 같은 번호는 세션 캐시로 재조회하지 않는다."""
    number = raw_number.strip()
    if not number:
        return None
    cache = st.session_state.setdefault("patent_cache", {})
    if number not in cache:
        with st.spinner("Google Patents에서 특허 요약과 청구항을 불러오는 중입니다..."):
            try:
                cache[number] = {"patent": fetch_patent(number), "error": None}
            except PatentLookupError as e:
                cache[number] = {"patent": None, "error": str(e)}
    entry = cache[number]
    if entry["error"]:
        st.error(entry["error"])
        return None

    p = entry["patent"]
    live_claims = [c for c in p["claims"] if c["text"].strip() not in ("삭제", "delete", "(deleted)")]
    col_msg, col_link = st.columns([4, 1])
    col_msg.success(f"특허 불러오기 완료: **{p['number']}** · {p['title']} · 청구항 {len(live_claims)}개")
    col_link.link_button("🔗 Google Patents", p["url"], width="stretch")
    with st.expander("불러온 특허 요약·청구항 미리보기"):
        st.markdown(f"**요약**\n\n{p['abstract'] or '(요약 정보 없음)'}")
        if live_claims:
            st.markdown("**청구범위**")
            st.text("\n\n".join(f"【청구항 {c['number']}】 {c['text']}" for c in live_claims))
    return p



render_header()
st.caption(f"현재 모드: {mode_label}")

if mode == "keyword":
    stack = st.text_area(
        "키워드 / 소자 스택 입력",
        placeholder="예: ITO / HAT-CN / NPB / CBP:Ir(ppy)3 / TPBi / LiF / Al",
        height=120,
    )
    problem = st.text_area(
        "해결하고 싶은 문제점",
        placeholder="예: 고휘도에서 효율 롤오프가 심하고 청색 수명이 짧음",
        height=100,
    )
    patent_number_input = st.text_input(
        "구글 특허 번호 입력 (선택)",
        placeholder="예: US11234567B2 또는 KR10-2023-0012345",
        help="특허 번호를 입력하시면 AI가 해당 특허의 핵심 요약과 청구항을 자동 반영하여 아이디어를 도출합니다.",
    )
    ref_patent = patent_lookup_block(patent_number_input)
elif mode == "single":
    st.markdown("### 📄 구글 특허 PDF 문서 업로드 및 분석")
    pdf, pdf_parsed = pdf_upload_block(
        "구글 특허 다운로드 PDF 파일을 업로드하세요",
        key="patent_pdf_upload",
        help="Google Patents에서 PDF로 다운로드한 특허 공보 문서를 업로드하면 자동으로 텍스트가 파싱됩니다.",
    )
    problem = st.text_area(
        "해당 특허 대비 해결·보완하고 싶은 중점 과제",
        placeholder="예: 해당 선행특허의 청구항 제1항 중 애노드 반사율 저감 구조를 우회하면서 광학 손실을 최소화하고 싶음",
        height=90,
    )
else:
    st.markdown("### 📑 두 특허 PDF 비교 융합")
    col1, col2 = st.columns(2)
    with col1:
        pdf_a, pdf_a_parsed = pdf_upload_block("특허 A 업로드 (PDF)", key="patent_a")
    with col2:
        pdf_b, pdf_b_parsed = pdf_upload_block("특허 B 업로드 (PDF)", key="patent_b")
    problem = st.text_area("추가로 고려할 문제점 (선택)", height=80)

def _collect_inputs() -> tuple[dict, str] | None:
    """현재 모드의 입력값을 검증해 (inputs, 이력용 입력 요약)으로 반환. 부족하면 경고 후 None."""
    if mode == "keyword":
        if patent_number_input.strip() and ref_patent is None:
            st.warning("입력한 특허 번호를 불러오지 못했습니다. 번호를 고치거나 비운 뒤 다시 시도해 주세요.")
            return None
        if not stack.strip() and ref_patent is None:
            st.warning("소자 스택·키워드를 입력하거나 구글 특허 번호를 입력해 주세요.")
            return None
        stack_text = stack.strip() or "(텍스트 입력 없음 - 참조 특허 기준으로 분석)"
        summary = stack_text + (f" | 문제: {problem.strip()}" if problem.strip() else "")
        inputs = {"stack": stack_text, "problem": problem}
        if ref_patent:
            # 번호만 넘기면 모델이 내용을 지어낼 수 있으므로, 실제로 가져온 원문을 전달한다
            inputs["reference_patent"] = patent_to_prompt_text(ref_patent)
            summary += f" | 참조 특허: {ref_patent['number']}"
        return inputs, summary
    # 특허 본문은 자르지 않고 전체를 전달한다 (청구범위가 보통 문서 끝부분에 있어, 앞부분만 보내면 청구항이 빠짐)
    if mode == "single":
        if pdf is None:
            st.warning("분석할 구글 특허 PDF 파일을 먼저 업로드해 주세요.")
            return None
        if pdf_parsed is None:
            st.warning("PDF 텍스트 추출에 실패해 분석할 수 없습니다. 다른 PDF를 업로드해 주세요.")
            return None
        return {"patent": pdf_parsed["text"], "problem": problem}, f"특허: {pdf.name}"
    if pdf_a is None or pdf_b is None:
        st.warning("비교할 특허 PDF 두 개를 모두 업로드해 주세요.")
        return None
    if pdf_a_parsed is None or pdf_b_parsed is None:
        st.warning("텍스트 추출에 실패한 PDF가 있어 분석할 수 없습니다.")
        return None
    inputs = {
        "patent_a": pdf_a_parsed["text"],
        "patent_b": pdf_b_parsed["text"],
        "problem": problem,
    }
    return inputs, f"특허 A: {pdf_a.name} × 특허 B: {pdf_b.name}"


ANALYZE_LABELS = {"single": "🚀 특허 문서 기반 분석 시작", "compare": "🚀 두 특허 비교 융합 분석 시작"}
if st.button(ANALYZE_LABELS.get(mode, "🔍 분석 시작"), type="primary", width="stretch", key="analyze_btn"):
    try:
        collected = _collect_inputs()
        if collected is not None:
            inputs, input_summary = collected
            with st.spinner("Claude가 분석 중입니다... (1~3분 정도 걸릴 수 있습니다)"):
                result = analyze_patent_data(
                    mode,
                    inputs,
                    history if use_history else None,
                    temperature=temperature,
                    priority_metric=priority_metric,
                    target_trl=target_trl,
                )
            # 가중 종합점수 계산·정렬에 쓰이도록 분석 조건을 결과에 함께 보관 (이력 복원 시에도 유지)
            result["settings"] = {
                "temperature": temperature,
                "priority_metric": priority_metric,
                "target_trl": target_trl,
            }
            st.session_state["result"] = result
            st.session_state["result_mode"] = mode_label
            # 이력 id = 현재 결과 id (아이디어별 구조도 저장 키의 접두사로도 사용)
            st.session_state["result_id"] = append_history(mode_label, input_summary, result)
            st.session_state["diagrams"] = {}  # 새 분석이면 이전 구조도·청구항은 비운다
            st.session_state["maturations"] = {}
            st.session_state["deep_analyses"] = {}
            st.session_state["kipris"] = {}
            st.rerun()  # 사이드바 히스토리에 새 이력을 반영
    except AnalyzerError as e:
        st.error(str(e))

st.divider()


# ---------------------------------------------------------------------------
# 결과 렌더링 헬퍼
# ---------------------------------------------------------------------------
def _idea_record(result_id: str, i: int, idea: dict) -> dict:
    """saved_ideas.json에 저장할 아이디어 레코드 (생성해 둔 구조도·청구항이 있으면 함께)."""
    key = f"{result_id}-{i}"
    return {
        "source_id": result_id,
        "idea_no": i,
        "analysis_title": result.get("title", ""),
        "analysis_settings": result.get("settings", {}),
        "idea": idea,
        "diagram_svg": st.session_state.get("diagrams", {}).get(key),
        "maturation": st.session_state.get("maturations", {}).get(key),
        "deep_analyses": st.session_state.get("deep_analyses", {}).get(key, {}),
        "kipris": st.session_state.get("kipris", {}).get(key),
    }


@st.cache_data(show_spinner=False, max_entries=20)
def _build_docx(title: str, claims_text: str, svg: str, summary: str, refs: list[dict]) -> bytes:
    """같은 내용이면 리런 때마다 Word 파일을 다시 만들지 않도록 캐시한다."""
    return generate_docx_report(title, claims_text, svg, summary=summary, reference_numerals=refs)


@st.cache_data(show_spinner=False, max_entries=5)
def _build_vault_docx(saved_json: str) -> bytes:
    """보관함 내용(JSON 문자열)이 같으면 Word 파일을 다시 만들지 않는다."""
    return generate_saved_ideas_docx(json.loads(saved_json))


def render_kipris_result(kres: dict) -> None:
    """KIPRIS 팩트체크: 실제 검색된 특허 목록(근거) + AI 대조 리포트."""
    queries = kres.get("queries", [])
    per_query = kres.get("per_query", {})
    st.caption("검색어: " + " · ".join(f"`{q}` ({per_query.get(q, 0)}건)" for q in queries))
    patents = kres.get("patents", [])
    if patents:
        st.markdown(f"**📑 KIPRIS 검색 선행특허 {len(patents)}건 (팩트체크 근거)**")
        st.dataframe(
            {
                "출원번호": [p.get("applicationNumber", "") for p in patents],
                "발명의 명칭": [p.get("inventionTitle", "") for p in patents],
                "출원인": [p.get("applicantName", "") for p in patents],
                "출원일": [p.get("applicationDate", "") for p in patents],
                "상태": [p.get("registerStatus", "") for p in patents],
                "검색어": [p.get("matched_query", "") for p in patents],
            },
            hide_index=True,
            width="stretch",
        )
    st.markdown(kres.get("report", ""))


def _safe_filename(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_")[:60] or "idea"


def _reset_sliders() -> None:
    for p in SIM_PARAMS:
        st.session_state[f"sim_{p['key']}"] = p["baseline"]


@st.fragment
def render_whatif_dashboard(idea: dict) -> None:
    """8각형 차트 + What-If 슬라이더. 슬라이더 조작 시 이 영역만 다시 그린다."""
    col_chart, col_sim = st.columns([3, 2], gap="large")
    with col_sim:
        st.markdown("**🎛️ What-If 파라미터 시뮬레이터**")
        values = {
            p["key"]: st.slider(
                p["label"],
                min_value=p["min"],
                max_value=p["max"],
                value=p["baseline"],
                step=p["step"],
                format=p["fmt"],
                key=f"sim_{p['key']}",
            )
            for p in SIM_PARAMS
        }
        st.button("기준값으로 초기화", on_click=_reset_sliders)
        st.caption(
            "※ 슬라이더 기준값은 AI 평가 시점의 가정 조건입니다. 점수 변화는 `utils.SIM_PARAMS`의 "
            "민감도 계수(가정값)로 계산되며, 실측·광학 시뮬레이션 데이터로 교체해 사용하세요."
        )

    base = idea["scores"]
    changed = any(values[p["key"]] != p["baseline"] for p in SIM_PARAMS)
    sim = simulate_scores(base, values) if changed else None
    with col_chart:
        st.markdown("**🎯 8대 지표 정량 평가 (10점 만점)**")
        st.plotly_chart(radar_chart(base, sim), width="stretch")

    if sim:
        st.dataframe(
            {
                "지표": [label for _, label in SCORE_METRICS],
                "AI 평가": [base[k] for k, _ in SCORE_METRICS],
                "시뮬레이션": [sim[k] for k, _ in SCORE_METRICS],
                "변화": [round(sim[k] - base[k], 1) for k, _ in SCORE_METRICS],
            },
            hide_index=True,
            width="stretch",
        )


# ---------------------------------------------------------------------------
# 결과 탭
# ---------------------------------------------------------------------------
tab_prior, tab_idea, tab_claims, tab_vault = st.tabs(
    ["1. 선행기술 분석", "2. 개선 아이디어", "3. 특허 청구항 초안", "📂 저장된 아이디어 보관함"]
)

result = st.session_state.get("result")
selected_idea = None

with tab_prior:
    if result:
        st.caption(f"분석 모드: {st.session_state.get('result_mode')}")
        st.markdown(prior_art_to_markdown(result))
    else:
        st.info("선행기술 분석 결과가 여기에 표시됩니다.")

with tab_idea:
    ideas = result.get("ideas", []) if result else []
    if not result:
        st.info("개선 아이디어가 여기에 표시됩니다.")
    elif not ideas:
        st.info("도출된 아이디어가 없습니다.")
    else:
        # --- 정량 평가 대시보드 ---
        st.subheader("📊 정량 평가 대시보드")
        settings = result.get("settings", {})
        priority_keys = PRIORITY_OPTIONS.get(settings.get("priority_metric"), ([], ""))[0]
        if settings:
            st.caption(f"분석 조건: {settings_summary(settings)}")
        idx = st.selectbox(
            "평가할 아이디어 선택",
            range(len(ideas)),
            format_func=lambda i: (
                f"아이디어 {i + 1}. {ideas[i]['title']}  "
                f"(TRL {ideas[i]['trl']} · 가중 종합 {weighted_score(ideas[i]['scores'], priority_keys):.1f})"
            ),
            help="가중 종합점수: 8개 지표 평균. '최우선 고려 지표'로 지정된 지표는 2배 가중치.",
        )
        selected_idea = ideas[idx]
        render_whatif_dashboard(selected_idea)

        st.markdown("**🧩 스택 레이어별 개선 기여도**")
        if selected_idea.get("layer_contributions"):
            st.plotly_chart(layer_contribution_chart(selected_idea["layer_contributions"]), width="stretch")
        else:
            st.caption("레이어별 기여도 데이터가 없습니다.")

        st.divider()

        # --- 전체 아이디어 일괄 저장 / JSON 다운로드 ---
        diagrams = st.session_state.setdefault("diagrams", {})
        result_id = st.session_state.setdefault("result_id", uuid.uuid4().hex[:8])
        col_save_all, col_dl_all = st.columns(2)
        with col_save_all:
            if st.button("💾 모든 아이디어 한 번에 저장하기", width="stretch"):
                total = save_ideas([_idea_record(result_id, i, idea) for i, idea in enumerate(ideas, 1)])
                st.success(f"아이디어 {len(ideas)}개가 성공적으로 저장되었습니다! (보관함 총 {total}개 · saved_ideas.json)")
        with col_dl_all:
            export = {
                "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "result": result,
                "ideas": [_idea_record(result_id, i, idea) for i, idea in enumerate(ideas, 1)],
            }
            st.download_button(
                "📥 전체 결과 세트 JSON 다운로드",
                data=json.dumps(export, ensure_ascii=False, indent=2),
                file_name=f"PRISM_Air_{_safe_filename(result.get('title', 'result'))}.json",
                mime="application/json",
                width="stretch",
            )

        # --- 아이디어 카드: 상세 + 온디맨드 구조도/심층 분석 ---
        deep = st.session_state.setdefault("deep_analyses", {})  # f"{result_id}-{i}" → {kind: 마크다운}
        kipris_results = st.session_state.setdefault("kipris", {})  # f"{result_id}-{i}" → 팩트체크 결과
        for i, idea in enumerate(ideas, 1):
            idea_key = f"{result_id}-{i}"
            idea_deep = deep.get(idea_key, {})
            with st.container(key=f"prism_card_idea_{idea_key}"):
                render_card_title(f"아이디어 {i}. {idea['title']}", idea.get("category_tag"))
                st.markdown(idea_to_markdown(i, idea))

                # 하단 버튼 6개를 한 줄에 배치 (이미 생성된 항목은 ✅ 표시, 다시 누르면 재생성)
                cols = st.columns(6)
                clicked_diagram = cols[0].button(
                    "🎨 구조도 상세 보기", key=f"diagram_btn_{idea_key}", width="stretch",
                    help="✅ 생성됨 · 다시 누르면 재생성" if idea_key in diagrams else "SVG 스택 단면도 생성",
                )
                clicked_deep = None
                for col, kind in zip(cols[1:4], DEEP_ANALYSES):
                    label = DEEP_ANALYSES[kind][0]
                    if col.button(label, key=f"deep_{kind}_{idea_key}", width="stretch",
                                  help="✅ 분석 완료 · 다시 누르면 재분석" if kind in idea_deep else f"{label} 실행"):
                        clicked_deep = kind
                clicked_kipris = cols[4].button(
                    "🔍 KIPRIS 팩트체크", key=f"kipris_{idea_key}", width="stretch",
                    help="✅ 대조 완료 · 다시 누르면 재검색" if idea_key in kipris_results
                    else "KIPRIS에서 실제 선행특허를 검색해 이 아이디어와 대조",
                )
                clicked_save = cols[5].button("💾 이 아이디어만 따로 저장", key=f"save_idea_{i}", width="stretch")

                # 버튼 처리 (스피너·알림은 카드 전체 폭으로 표시)
                try:
                    if clicked_diagram:
                        with st.spinner("Claude가 스택 구조 단면도를 그리는 중입니다... (30초~1분)"):
                            diagrams[idea_key] = generate_svg_diagram(idea_to_diagram_text(idea, result))
                        save_history_diagram(result_id, i, diagrams[idea_key])  # 이력에도 저장
                    if clicked_deep:
                        fn = DEEP_ANALYSIS_FUNCS[clicked_deep]
                        with st.spinner(f"Claude가 {DEEP_ANALYSES[clicked_deep][0]}을 수행 중입니다... (1~2분)"):
                            text = fn(idea_to_diagram_text(idea, result))
                        deep.setdefault(idea_key, {})[clicked_deep] = text
                        save_history_deep_analysis(result_id, i, clicked_deep, text)
                        st.session_state["deep_last"] = (idea_key, clicked_deep)
                    if clicked_kipris:
                        with st.spinner("특허청 KIPRIS DB에서 선행특허를 실시간 검색 및 대조 중입니다... (1~2분)"):
                            kres = search_kipris_and_fact_check(idea["title"], idea_to_diagram_text(idea, result))
                        kipris_results[idea_key] = kres
                        save_history_kipris(result_id, i, kres)
                        st.session_state["deep_last"] = (idea_key, "kipris")
                except (AnalyzerError, KiprisError) as e:
                    st.error(str(e))
                if clicked_save:
                    save_ideas([_idea_record(result_id, i, idea)])
                    st.success("아이디어가 성공적으로 저장되었습니다!")

                # 결과 표시
                if idea_key in diagrams:
                    with st.expander("🧱 스택 구조 단면도 (Visual Stack Schema)", expanded=True):
                        render_stack_diagram(diagrams[idea_key])
                for kind, (label, _) in DEEP_ANALYSES.items():
                    if kind in deep.get(idea_key, {}):
                        just_made = st.session_state.get("deep_last") == (idea_key, kind)
                        with st.expander(f"{label} 결과", expanded=just_made):
                            st.markdown(deep[idea_key][kind])
                if idea_key in kipris_results:
                    just_made = st.session_state.get("deep_last") == (idea_key, "kipris")
                    with st.expander("🏛️ KIPRIS 실시간 선행특허 대조 팩트체크 결과 보기", expanded=just_made):
                        render_kipris_result(kipris_results[idea_key])

with tab_claims:
    ideas = result.get("ideas", []) if result else []
    if not result:
        st.info("특허 청구항 초안이 여기에 표시됩니다.")
    elif not ideas:
        st.info("고도화할 아이디어가 없습니다.")
    else:
        maturations = st.session_state.setdefault("maturations", {})
        diagrams = st.session_state.setdefault("diagrams", {})
        result_id = st.session_state.setdefault("result_id", uuid.uuid4().hex[:8])

        st.markdown("##### 🧪 아이디어 고도화 (Maturation)")
        st.caption("2번 탭의 아이디어 중 하나를 골라 독립항·종속항과 대표 도면을 생성합니다. 버튼을 누를 때만 API를 호출합니다.")
        m_idx = st.selectbox(
            "고도화할 아이디어 선택",
            range(len(ideas)),
            format_func=lambda i: (
                f"아이디어 {i + 1}. {ideas[i]['title']}"
                + ("  ✅ 생성됨" if f"{result_id}-{i + 1}" in maturations else "")
            ),
        )
        m_no = m_idx + 1
        m_idea = ideas[m_idx]
        m_key = f"{result_id}-{m_no}"

        btn_label = "🔄 청구항 및 도면 다시 생성" if m_key in maturations else "📜 선택한 아이디어의 청구항 및 도면 생성"
        if st.button(btn_label, type="primary", key=f"mature_btn_{m_key}"):
            try:
                with st.spinner("Claude가 청구항과 도면을 작성 중입니다... (1~2분 정도 걸릴 수 있습니다)"):
                    data = generate_claims_and_diagram(idea_to_diagram_text(m_idea, result))
                maturations[m_key] = data
                save_history_maturation(result_id, m_no, data)
                # 2번 탭 구조도가 아직 없으면 이번 도면을 공유 (추가 API 호출 절약)
                if m_key not in diagrams and data.get("svg"):
                    diagrams[m_key] = data["svg"]
                    save_history_diagram(result_id, m_no, data["svg"])
                st.rerun()
            except AnalyzerError as e:
                st.error(str(e))

        if m_key in maturations:
            m = maturations[m_key]
            st.divider()
            st.markdown(f"### 【발명의 명칭】 {m_idea['title']}")
            st.markdown("#### 【청구범위】")
            st.markdown(claims_to_markdown(m.get("claims", [])))

            st.markdown("#### 【도면】")
            render_stack_diagram(m.get("svg", ""))

            refs = m.get("reference_numerals", [])
            if refs:
                st.markdown("#### 【부호의 설명】")
                st.dataframe(
                    {"부호": [r["numeral"] for r in refs], "구성요소": [r["name"] for r in refs]},
                    hide_index=True,
                )

            svg_clean = sanitize_svg(m.get("svg", "")) or ""
            docx_bytes = _build_docx(
                m_idea["title"],
                claims_to_text(m.get("claims", [])),
                svg_clean,
                f"{m_idea['description']}\n\n기대 효과: {m_idea['expected_effect']}",
                refs,
            )
            st.download_button(
                label="📥 특허 명세서 초안 다운로드 (.docx)",
                data=docx_bytes,
                file_name=f"PRISM_Air_명세서초안_{_safe_filename(m_idea['title'])}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary",
                key=f"docx_dl_{m_key}",
            )
        else:
            st.info("아이디어를 선택하고 '📜 선택한 아이디어의 청구항 및 도면 생성' 버튼을 눌러 주세요.")

        # 이전 버전(최초 분석에서 청구항까지 생성하던 시절) 결과 호환
        if result.get("claims"):
            with st.expander("📁 이전 버전 분석에서 생성된 청구항 초안"):
                st.markdown(claims_to_markdown(result["claims"]))

with tab_vault:
    # 2번 탭에서 방금 저장한 내용까지 반영되도록 탭을 그리는 시점에 파일을 다시 읽는다
    saved_ideas = load_saved_ideas()
    if not saved_ideas:
        st.info("아직 보관된 아이디어가 없습니다. 2번 탭에서 마음에 드는 아이디어를 저장해 보세요!")
    else:
        col_info, col_dl = st.columns([3, 2])
        col_info.markdown(f"##### 📂 보관 아이디어 {len(saved_ideas)}건")
        with col_dl:
            st.download_button(
                "📥 보관함 전체 내용 워드(.docx)로 내보내기",
                data=_build_vault_docx(json.dumps(saved_ideas, ensure_ascii=False, sort_keys=True)),
                file_name=f"PRISM_Air_아이디어보관함_{datetime.now():%Y%m%d}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                width="stretch",
            )

        for s in reversed(saved_ideas):  # 최근 저장 순
            item_id = saved_item_id(s)
            v_idea = s.get("idea", {})
            with st.container(key=f"prism_card_vault_{item_id}"):
                col_text, col_chart = st.columns([3, 2], gap="medium")
                with col_text:
                    render_card_title(v_idea.get("title", "(제목 없음)"), v_idea.get("category_tag"))
                    badges = [trl_badge(v_idea.get("trl", 1))]
                    if s.get("maturation"):
                        badges.append(":primary-badge[:material/gavel: 청구항 생성됨]")
                    if s.get("deep_analyses"):
                        badges.append(f":gray-badge[:material/insights: 심층 분석 {len(s['deep_analyses'])}건]")
                    if s.get("diagram_svg") or (s.get("maturation") or {}).get("svg"):
                        badges.append(":gray-badge[:material/layers: 구조도 포함]")
                    st.markdown(" ".join(badges))
                    st.caption(f"🕒 저장 일시 {s.get('saved_at', '-')}  ·  출처 분석: {s.get('analysis_title') or '-'}")
                    st.markdown(f"**핵심 요약:** {v_idea.get('expected_effect', '-')}")
                    if v_idea.get("contradiction"):
                        st.markdown(f"**기술적 모순:** {v_idea['contradiction']}")
                    if st.button("🗑️ 보관함에서 삭제", key=f"del_{item_id}"):
                        delete_saved_idea(item_id)
                        st.toast(f"'{v_idea.get('title', '')}' 아이디어를 보관함에서 삭제했습니다.", icon="🗑️")
                        st.rerun()
                with col_chart:
                    if v_idea.get("scores"):
                        st.plotly_chart(
                            radar_chart(v_idea["scores"], height=300),
                            width="stretch",
                            key=f"vault_radar_{item_id}",
                            config={"displayModeBar": False},
                        )

                with st.expander("상세 내용 보기"):
                    st.markdown(v_idea.get("description", ""))
                    if v_idea.get("layer_contributions"):
                        st.caption(
                            "레이어별 기여도: "
                            + ", ".join(f"{c['layer']} {c['percent']:.0f}%" for c in v_idea["layer_contributions"])
                        )
                    v_svg = s.get("diagram_svg") or (s.get("maturation") or {}).get("svg")
                    if v_svg:
                        render_stack_diagram(v_svg)
                    if (s.get("maturation") or {}).get("claims"):
                        st.markdown("**청구항 초안**")
                        st.markdown(claims_to_markdown(s["maturation"]["claims"]))
                    for kind, text in (s.get("deep_analyses") or {}).items():
                        if kind in DEEP_ANALYSES and text:
                            st.markdown(f"---\n**{DEEP_ANALYSES[kind][0]}**")
                            st.markdown(text)
                    if s.get("kipris"):
                        st.markdown("---\n**🏛️ KIPRIS 선행특허 팩트체크**")
                        render_kipris_result(s["kipris"])

# ---------------------------------------------------------------------------
# 하단: 레퍼런스 검색 링크 (키워드는 LLM, URL은 코드에서 조립)
# ---------------------------------------------------------------------------
if result:
    st.divider()
    st.subheader("📚 레퍼런스 검색")
    prior_kw = result.get("prior_art", {}).get("search_keywords", [])
    idea_kw = selected_idea.get("search_keywords", []) if selected_idea else []

    if prior_kw:
        st.caption("선행기술 키워드: " + ", ".join(f"`{k}`" for k in prior_kw))
    if idea_kw:
        st.caption("선택 아이디어 키워드: " +", ".join(f"`{k}`" for k in idea_kw))

    links = []
    if prior_kw:
        links += [
            ("🎓 Google Scholar · 선행기술 논문", scholar_url(prior_kw)),
            ("📄 Google Patents · 선행기술 특허", patents_url(prior_kw)),
        ]
    if idea_kw:
        links.append(("🔎 Google Patents · 선택 아이디어 회피성 확인", patents_url(idea_kw)))

    if links:
        for col, (label, url) in zip(st.columns(len(links)), links):
            col.link_button(label, url, width="stretch")
    else:
        st.caption("검색 키워드가 없습니다.")
