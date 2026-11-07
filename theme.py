"""PRISM Air 애플 미니멀리즘 스타일 (커스텀 CSS + HTML 조각)."""

from html import escape

import streamlit as st

PRISM_CSS = """
<style>
    /* 1. 애플 시그니처 배경 및 시스템 폰트 */
    .stApp {
        background-color: #F5F5F7;
        font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Helvetica, Arial, sans-serif;
        color: #1D1D1F;
    }

    /* 2. 사이드바 - 애플 미니멀리즘 스타일
       흰색 카드가 구분되어 보이도록 사이드바 바탕은 앱 배경과 같은 옅은 그레이로 둔다. */
    [data-testid="stSidebar"] {
        background-color: #F5F5F7;
        border-right: 0.5px solid #E5E5EA;
    }

    /* 사이드바 섹션 카드 (.sidebar-card / key="sidebar_card_..." 컨테이너) */
    .sidebar-card,
    [class*="st-key-sidebar_card_"] {
        background: #FFFFFF;
        border-radius: 14px;
        border: 0.5px solid #E5E5EA;
        padding: 16px;
        margin-bottom: 16px;
    }
    .sidebar-brand {
        font-size: 1.15rem;
        font-weight: 800;
        color: #0F172A;
        letter-spacing: -0.02em;
        margin: 0 0 4px 2px;
    }
    .sidebar-card-title {
        font-size: 0.95rem;
        font-weight: 700;
        color: #1D1D1F;
        letter-spacing: -0.01em;
        margin: 0 0 2px 0;
    }

    /* 3. 헤더 타이틀 (슬림 헤더 카드 안에서 사용) */
    .main-title {
        font-size: 1.8rem;
        font-weight: 800;
        color: #0F172A;
        letter-spacing: -0.03em;
        line-height: 1.15;
    }
    .sub-title {
        font-size: 1rem;
        font-weight: 600;
        color: #475569;
        letter-spacing: -0.01em;
    }
    .desc-text {
        font-size: 0.85rem;
        color: #64748B;
        letter-spacing: -0.01em;
    }

    /* 헤더 카드: 슬림한 한 블록 (타이틀+부제 한 줄, 설명 한 줄) */
    .prism-header-card {
        background: linear-gradient(135deg, #F8FAFC 0%, #F1F5F9 100%);  /* 옅은 블루그레이 틴트 */
        border: 1px solid #E2E8F0;
        border-radius: 14px;
        padding: 16px 24px;
        margin-bottom: 1rem;
    }
    .prism-header-card p { margin: 0; }  /* Streamlit 기본 문단 여백 제거 → 높이 최소화 */
    .prism-header-row {
        display: flex;
        align-items: baseline;   /* 큰 타이틀과 부제를 글자 기준선에 맞춰 정렬 */
        flex-wrap: wrap;         /* 좁은 화면에서는 부제가 다음 줄로 */
        column-gap: 14px;
        row-gap: 2px;
    }
    .prism-header-card .desc-text { margin-top: 4px; }

    /* 4. 애플 스타일 카드 (Mac 카드가 의도된 둥근 모서리와 부드러운 깊이)
       Streamlit 위젯은 임의의 <div>로 감쌀 수 없으므로, key="prism_card_..."로 만든
       st.container에 Streamlit이 붙여 주는 클래스(st-key-prism_card_...)에 같은 스타일을 적용한다. */
    .prism-card,
    [class*="st-key-prism_card_"] {
        background: #FFFFFF;
        border: 0.5px solid #E5E5EA;
        border-radius: 18px;
        padding: 28px;
        margin-bottom: 20px;
        box-shadow: 0 4px 24px rgba(0, 0, 0, 0.03);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .prism-card:hover,
    [class*="st-key-prism_card_"]:hover {
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.06);
        border-color: #D2D2D7;
    }

    /* 카드 제목 */
    .card-title {
        font-size: 1.25rem;
        font-weight: 600;
        color: #1D1D1F;
        letter-spacing: -0.02em;
        margin: 0 0 0.4rem 0;
        line-height: 1.5;
    }
    .card-title .apple-badge {
        margin-left: 8px;
        vertical-align: middle;
    }

    /* 5. 세련된 미니멀 뱃지 (Pill) */
    .apple-badge {
        background-color: #F5F5F7;
        color: #1D1D1F;
        padding: 5px 12px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: -0.01em;
        display: inline-block;
        border: 0.5px solid #D2D2D7;
    }
</style>
"""


def inject_css() -> None:
    st.markdown(PRISM_CSS, unsafe_allow_html=True)


def render_header() -> None:
    """슬림 헤더 카드: 1줄째 타이틀+영문 부제(기준선 정렬), 2줄째 한국어 설명."""
    st.markdown(
        '<div class="prism-header-card">'
        '<div class="prism-header-row">'
        '<span class="main-title">🌬️ PRISM Air</span>'
        '<span class="sub-title">Problem-driven R&amp;D Idea Scouting &amp; Maturation</span>'
        "</div>"
        '<p class="desc-text">디스플레이 R&amp;D 문제 기반 아이디어 발굴 및 특허 후보 도출 자동화 Tool</p>'
        "</div>",
        unsafe_allow_html=True,
    )


def category_pill(tag: str | None) -> str:
    """category_tag → 미니멀 pill 배지 HTML (태그 앞의 이모지로 성격 구분). 태그가 없으면 빈 문자열."""
    if not tag:
        return ""
    return f'<span class="apple-badge">{escape(tag)}</span>'


def render_card_title(title: str, tag: str | None = None, prefix: str = "💡") -> None:
    """카드 제목 + 성격 태그 pill. 제목은 LLM이 생성한 텍스트이므로 HTML 이스케이프한다."""
    st.markdown(
        f'<p class="card-title">{prefix} {escape(title)}{category_pill(tag)}</p>',
        unsafe_allow_html=True,
    )
