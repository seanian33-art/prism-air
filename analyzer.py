"""특허 분석 로직: Anthropic Claude API 호출 및 결과 파싱."""

import base64
import json
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-5-5"
PLACEHOLDER_KEY = "여기에_키_입력"

PERSONA = "너는 20년 이상 OLED 소자/재료 공학을 연구한 수석 엔지니어이자 수석 변리사야."

# 아이디어 성격 태그 (enum으로 응답 강제)
TAG_DESIGN_AROUND = "🛡️ 특허 회피 중심 (Design-Around)"
TAG_TRIZ = "⚙️ TRIZ 모순 극복 중심 (Trade-off Breaking)"
TAG_FAB = "🏭 양산성 및 공정 마진 중심 (Fab Margin)"
CATEGORY_TAGS = [TAG_DESIGN_AROUND, TAG_TRIZ, TAG_FAB]

# 온디맨드 단면도 생성(generate_svg_diagram) 전용 규칙
STACK_DIAGRAM_RULES = (
    "<stack_diagram_rules>\n"
    "형식\n"
    "- 단일 <svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 900 H\"> 요소 하나만 출력 (H는 내용에 맞게 500~1100). "
    "width/height 속성은 넣지 말 것. <script>, 이벤트 속성(onclick 등), 외부 이미지·폰트·링크는 금지.\n"
    "- 배경은 흰색 <rect>로 채우고, 글꼴은 font-family=\"'Malgun Gothic','Apple SD Gothic Neo',sans-serif\".\n"
    "- 상단에 다이어그램 제목(아이디어 이름)을 굵게 표기.\n\n"
    "레이어 표현\n"
    "- 실제 적층 순서대로 각 층을 가로로 긴 직사각형(x≈80, 폭≈480)으로 쌓아 단면을 표현. "
    "기판이 아래, 광 출사면/봉지가 위에 오도록(배면 발광이면 그 반대로 명시).\n"
    "- 층 두께는 상대적인 차이가 보이도록 높이를 다르게 하되, 얇은 층도 글자가 들어가도록 최소 높이 22px.\n"
    "- 각 층 내부 또는 우측(x≈580 이후)에 층 이름, 재료, 두께(nm)를 표기하고, 우측 표기는 지시선으로 해당 층에 연결.\n"
    "- 색상 규칙:\n"
    "  · 컬러필터 CF: R=#E53935, G=#43A047, B=#1E88E5 (서브픽셀로 분할해서 나란히 배치)\n"
    "  · 블랙 매트릭스 BM: #111111 (CF 서브픽셀 사이)\n"
    "  · TFE/봉지(무기층): #9E9E9E, fill-opacity 0.45 / 유기 버퍼층: #BDBDBD, fill-opacity 0.35\n"
    "  · 편광판/커버 윈도우: #CFD8DC 반투명\n"
    "  · 캐소드(Mg:Ag, Al 등): #B0BEC5 / 애노드(ITO): #FFE082 / 반사 애노드(Ag): #ECEFF1 에 진한 테두리\n"
    "  · HIL/HTL: #AB47BC 계열, EBL: #7E57C2, EML: 발광색에 맞게(청 #42A5F5, 녹 #66BB6A, 적 #EF5350), "
    "HBL: #26A69A, ETL/EIL: #FFA726 계열, CPL: #80DEEA\n"
    "  · TFT/백플레인: #78909C, 기판(유리/PI): #E0E0E0\n"
    "- 이 아이디어로 새로 추가·변경된 층은 빨간 점선 테두리(stroke=#D50000, stroke-dasharray)와 'NEW' 또는 '변경' 배지로 강조.\n\n"
    "계면과 광학 경로\n"
    "- 핵심 계면(예: EML/ETL, 캐소드/CPL)은 층 경계에 짧은 표시선과 '계면: ...' 텍스트로 표기하고, 그 계면에서 일어나는 현상(엑시톤 재결합, 전하 차단, 굴절률 차이 등)을 짧게 적을 것.\n"
    "- 광학 진행 방향을 화살표(<marker> 화살촉 정의 후 line/path에 marker-end 적용)로 그릴 것: "
    "발광/출광 경로는 노란색~주황색 실선 화살표, 외광 입사 및 반사(반사 방지 원리)는 회색 점선 화살표, "
    "도파·흡수 손실은 빨간 X 표시나 짧은 화살표로 표시. 각 화살표 옆에 '출광', '외광 반사 억제', '도파 모드 손실 감소' 등 텍스트 라벨.\n"
    "- 하단에 범례(색상 의미, 화살표 의미)와 핵심 개선 포인트 1~2줄 요약 박스를 넣을 것.\n"
    "- 모든 텍스트가 서로 겹치거나 viewBox 밖으로 잘리지 않도록 좌표를 계산할 것.\n"
    "</stack_diagram_rules>"
)

SYSTEM_PROMPT = (
    f"{PERSONA} "
    "입력된 스택과 문제점을 분석해서 1)선행기술 취약점, 2)TRIZ 기반 개선 아이디어를 JSON 형태로 도출해. "
    "특허 청구항은 작성하지 마 (사용자가 아이디어를 골라 고도화할 때 별도로 생성한다).\n\n"
    "모든 응답 내용은 한국어로 작성하고, 재료명·물성값·소자 구조는 가능한 한 구체적으로 적어.\n\n"
    "개선 아이디어는 3~4개를 도출해. 단면도·SVG·HTML 코드는 만들지 말고 텍스트와 정량 데이터만 작성해 "
    "(구조도는 사용자가 요청할 때 별도로 생성한다). 대신 description에는 적층 순서와 "
    "추가·변경된 층, 각 층의 재료와 두께를 구체적으로 적어 나중에 단면도를 그릴 수 있게 해.\n\n"
    "<category_tag>\n"
    "각 아이디어의 성격을 다음 3가지 중 가장 적합한 하나로 판별해 category_tag에 그대로 적어.\n"
    "- '🛡️ 특허 회피 중심 (Design-Around)': 기존 특허 권리범위를 우회·차별화하는 것이 핵심 가치인 아이디어\n"
    "- '⚙️ TRIZ 모순 극복 중심 (Trade-off Breaking)': 반사율↔출광효율처럼 상충하는 성능을 동시에 개선하는 것이 핵심인 아이디어\n"
    "- '🏭 양산성 및 공정 마진 중심 (Fab Margin)': 공정 단계·수율·비용·라인 호환성 개선이 핵심인 아이디어\n"
    "가능하면 아이디어 세트 안에서 성격이 한쪽으로만 몰리지 않게 구성해.\n"
    "</category_tag>\n\n"
    "<history_title>\n"
    "title 필드에는 사용자가 입력한 핵심 키워드(스택·문제점 또는 특허 주제)를 바탕으로 "
    "이번 분석을 대표하는 짧은 한국어 제목을 8~20자로 작성해. 예: 'OLED COE 구조 개선', '청색 TADF 수명 향상'.\n"
    "</history_title>\n\n"
    "<quantitative_evaluation>\n"
    "각 아이디어마다 다음 정량 데이터를 반드시 채워.\n"
    "- scores: 8개 지표를 1~10 정수로 평가 (10이 가장 유리). 지표 정의:\n"
    "  · reflection_reduction(반사 저감): 외광 반사 억제 성능\n"
    "  · outcoupling(출광 효율): 내부 발광의 외부 추출 효율\n"
    "  · material_fit(재료 적합성): 제안 재료의 물성·공정 호환성\n"
    "  · drive_reliability(구동 신뢰성): 수명, 열/수분 안정성, 구동 전압 안정성\n"
    "  · manufacturability(제조 용이성): 공정 단계 수, 수율, 기존 라인 호환성\n"
    "  · cost(양산 비용): 비용 경쟁력 (10 = 추가 비용 거의 없음)\n"
    "  · patent_avoidance(특허 회피성): 기존 특허 대비 회피·차별화 가능성\n"
    "  · impact(기술 파급력): 제품군 확장성, 후속 특허 파생 가능성\n"
    "  점수는 근거 있게 차등을 둘 것. 모든 지표를 높게 주는 낙관적 평가 금지.\n"
    "- layer_contributions: 이 아이디어의 개선 효과에 각 스택 레이어(TFE, BM, CF, PDL, EML, CPL, 캐소드 등)가 "
    "기여하는 비율(%). 관련 레이어만 나열하고 합계가 100이 되게 할 것.\n"
    "- trl: 현업 적용 가능성을 기술성숙도 TRL 1~9 정수로 판정하고, trl_rationale에 근거를 1~2문장으로.\n"
    "</quantitative_evaluation>\n\n"
    "<search_keywords>\n"
    "URL은 절대 생성하지 마. 대신 문헌·특허 검색에 바로 쓸 수 있는 전문 키워드만 추출해.\n"
    "- prior_art.search_keywords와 각 아이디어의 search_keywords에 3~5개씩.\n"
    "- 검색 적중률을 위해 영어 기술 용어로 작성 (예: 'thin film encapsulation', 'black matrix', "
    "'polarizer-free OLED', 'color filter on encapsulation'). 하나의 키워드는 1~4단어.\n"
    "</search_keywords>\n\n"
    "<history_policy>\n"
    "입력에 <analysis_history>가 주어지면 그것은 이 시스템에서 과거에 도출된 아이디어 목록이다. "
    "과거 이력을 회피하거나 융합 발전시킨 완전히 새로운 차세대 아이디어를 도출하라. "
    "과거 아이디어와 핵심 메커니즘이 같은 아이디어는 다시 내지 말고, "
    "각 아이디어의 history_relation에 과거 이력과의 관계(회피/융합 발전/신규)를 한 문장으로 적어. "
    "이력이 없으면 '신규'라고 적어.\n"
    "</history_policy>"
)

# 8각형 차트 지표 (JSON 키, 화면 표시명) - 순서가 차트 축 순서
SCORE_METRICS = [
    ("reflection_reduction", "반사 저감"),
    ("outcoupling", "출광 효율"),
    ("material_fit", "재료 적합성"),
    ("drive_reliability", "구동 신뢰성"),
    ("manufacturability", "제조 용이성"),
    ("cost", "양산 비용"),
    ("patent_avoidance", "특허 회피성"),
    ("impact", "기술 파급력"),
]

HISTORY_CONTEXT_LIMIT = 20  # 모델에 전달할 최근 이력 개수

# ---------------------------------------------------------------------------
# 사이드바 'AI 분석 엔진 세부 설정' 옵션 (app.py와 공유)
# ---------------------------------------------------------------------------
DEFAULT_PRIORITY = "균형 잡힌 분석 (기본)"
# 최우선 지표 → (가중치 2배를 줄 지표 키, 아이디어 설계 지침)
PRIORITY_OPTIONS = {
    DEFAULT_PRIORITY: ([], ""),
    "특허 회피 최우선": (
        ["patent_avoidance"],
        "기존 특허(특히 선행기술로 분석한 특허)의 독립항 구성요소를 하나 이상 확실히 결여하거나 치환하는 "
        "구조·재료·공정을 설계하고, 차별화 포인트를 description에 명시해.",
    ),
    "양산 비용 절감 최우선": (
        ["cost", "manufacturability"],
        "추가 마스크·공정 단계와 신규 장비 투자를 최소화하고, 기존 양산 라인과 검증된 재료를 우선 활용해. "
        "공정 단계 수 변화와 예상 원가 영향을 description에 명시해.",
    ),
    "광학 성능 최적화 최우선": (
        ["reflection_reduction", "outcoupling"],
        "외광 반사율 저감과 출광 효율 향상을 최우선 목표로 하고, 굴절률·두께·OD 등 광학 설계 수치를 "
        "description에 구체적으로 명시해.",
    ),
}

DEFAULT_TRL = "상관없음 (전체 탐색)"
TRL_OPTIONS = {
    DEFAULT_TRL: None,
    "단기 양산 적용 (TRL 7~9)": (7, 9),
    "중장기 선행 개발 (TRL 4~6)": (4, 6),
}


def _creativity_guideline(temperature: float) -> str:
    """'창의성' 슬라이더 값을 프롬프트 지시로 변환한다.

    현재 Claude 모델(4.7 이후, claude-sonnet-5-5 포함)은 API의 temperature를 기본값 외에는
    400 오류로 거부하므로, 샘플링 파라미터 대신 아이디어 탐색 범위를 프롬프트로 조절한다.
    """
    if temperature <= 0.3:
        style = (
            "보수적·현실적 탐색: 양산 검증된 재료와 공정 안에서의 점진적 개선 위주로 도출해. "
            "실현 리스크가 큰 신규 메커니즘은 제외해."
        )
    elif temperature <= 0.6:
        style = (
            "균형 탐색: 검증된 기술 기반의 개선안을 중심으로 하되, 1개 정도는 "
            "새로운 메커니즘이나 이종 기술 융합을 시도한 도전적 아이디어를 포함해."
        )
    else:
        style = (
            "혁신적·파괴적 탐색: 업계에서 당연하게 여기는 전제를 깨는 비자명한 아이디어를 우선해. "
            "타 분야(반도체, 광학 메타표면, 바이오 소재 등)의 원리 차용, 새로운 물리 메커니즘을 적극 활용하고, "
            "높은 기술 리스크를 감수해도 좋다. 최소 2개는 기존에 없던 접근이어야 한다."
        )
    return f"아이디어 창의성 수준 {temperature:.1f} / 1.0 → {style}"


def build_system_prompt(temperature: float, priority_metric: str, target_trl: str) -> str:
    """사이드바 설정을 반영한 시스템 프롬프트를 동적으로 구성한다."""
    conditions = [f"- {_creativity_guideline(temperature)}"]

    metric_keys, guideline = PRIORITY_OPTIONS.get(priority_metric, ([], ""))
    if metric_keys:
        labels = ", ".join(label for key, label in SCORE_METRICS if key in metric_keys)
        conditions.append(
            f"- 최우선 고려 지표: '{priority_metric}' → 모든 아이디어는 [{labels}] 지표에서 "
            f"뚜렷한 우위(8점 이상 목표)를 갖도록 설계해. {guideline} "
            f"종합 판단 시 [{labels}] 지표에 2배 가중치를 부여하고, ideas 배열은 가중 종합점수가 높은 순으로 정렬해. "
            "단, 점수 자체를 부풀리지 말고 실제 근거에 맞게 매겨라 (가중치는 아이디어 선정·정렬에 반영)."
        )

    trl_range = TRL_OPTIONS.get(target_trl)
    if trl_range:
        lo, hi = trl_range
        conditions.append(
            f"- 목표 기술성숙도: '{target_trl}' → 모든 아이디어는 TRL {lo}~{hi} 범위에 들어가야 한다. "
            + ("검증된 재료·공정 조합과 기존 장비로 1~2년 내 양산 적용 가능한 수준으로 구체화하고, "
               "구동 신뢰성·제조 용이성 평가를 엄격히 해."
               if lo >= 7 else
               "실험실 수준의 원리 검증은 가능하지만 양산까지 3~5년이 필요한 선행 개발 과제로 구체화하고, "
               "기술 파급력과 특허 선점 가치를 중시해.")
            + " trl_rationale에 이 범위에 해당하는 근거를 명시해."
        )

    return (
        f"{SYSTEM_PROMPT}\n\n"
        "<user_conditions>\n"
        "사용자가 설정한 분석 조건이다. 특정 최우선 고려 지표나 목표 TRL이 설정되어 있다면, "
        "반드시 그 조건에 부합하는 방향으로 개선 아이디어를 도출하고 정량 평가에도 가중치를 부여하라.\n"
        + "\n".join(conditions)
        + "\n</user_conditions>"
    )

MODE_INSTRUCTIONS = {
    "keyword": "다음은 사용자가 입력한 OLED 소자 스택/키워드와 해결하고 싶은 문제점이다.",
    "single": "다음은 업로드된 단일 특허 문서의 본문이다. 이 특허를 선행기술로 보고 분석해.",
    "compare": (
        "다음은 두 개의 특허 문서(특허 A, 특허 B)다. 두 특허의 기술을 비교하고, "
        "각각의 한계를 보완하도록 두 기술을 융합한 개선 아이디어를 도출해."
    ),
}

# Claude 응답을 이 스키마에 맞는 JSON으로 강제한다.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "분석 이력용 짧은 제목 (8~20자)"},
        "prior_art": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "입력 기술/선행기술의 핵심 요약"},
                "weaknesses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["title", "description"],
                        "additionalProperties": False,
                    },
                },
                "search_keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "weaknesses", "search_keywords"],
            "additionalProperties": False,
        },
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "contradiction": {"type": "string", "description": "해결하려는 기술적 모순"},
                    "triz_principles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "적용한 TRIZ 발명 원리 (예: '#1 분할')",
                    },
                    "description": {"type": "string"},
                    "expected_effect": {"type": "string"},
                    "scores": {
                        "type": "object",
                        "properties": {
                            key: {"type": "integer", "description": f"{label} (1~10)"}
                            for key, label in SCORE_METRICS
                        },
                        "required": [key for key, _ in SCORE_METRICS],
                        "additionalProperties": False,
                    },
                    "layer_contributions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layer": {"type": "string"},
                                "percent": {"type": "number"},
                            },
                            "required": ["layer", "percent"],
                            "additionalProperties": False,
                        },
                    },
                    "trl": {"type": "integer", "description": "기술성숙도 TRL (1~9)"},
                    "trl_rationale": {"type": "string"},
                    "search_keywords": {"type": "array", "items": {"type": "string"}},
                    "history_relation": {"type": "string"},
                    "category_tag": {"type": "string", "enum": CATEGORY_TAGS},
                },
                "required": [
                    "title",
                    "contradiction",
                    "triz_principles",
                    "description",
                    "expected_effect",
                    "scores",
                    "layer_contributions",
                    "trl",
                    "trl_rationale",
                    "search_keywords",
                    "history_relation",
                    "category_tag",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "prior_art", "ideas"],
    "additionalProperties": False,
}


class AnalyzerError(Exception):
    """사용자에게 보여줄 수 있는 분석 오류."""


def _get_client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key or api_key == PLACEHOLDER_KEY:
        raise AnalyzerError(".env 파일의 ANTHROPIC_API_KEY에 실제 API 키를 입력해 주세요.")
    return anthropic.Anthropic(api_key=api_key)


def _build_user_message(mode: str, inputs: dict) -> str:
    header = MODE_INSTRUCTIONS[mode]
    if mode == "keyword":
        body = (
            f"<stack>\n{inputs.get('stack', '').strip()}\n</stack>\n\n"
            f"<problem>\n{inputs.get('problem', '').strip() or '(명시되지 않음)'}\n</problem>"
        )
        if inputs.get("reference_patent"):
            body += (
                "\n\n아래 <reference_patent>는 사용자가 지정한 참조 특허를 Google Patents에서 가져온 실제 원문"
                "(서지·요약·청구범위)이다. 이 특허를 핵심 선행기술로 삼아 독립항 구성요소 기준으로 취약점을 분석하고, "
                "사용자 스택·문제점과의 관계를 반영해 아이디어를 도출해. 이 원문에 없는 내용을 이 특허의 내용처럼 서술하지 마.\n"
                f"<reference_patent>\n{inputs['reference_patent']}\n</reference_patent>"
            )
    elif mode == "single":
        body = f"<patent>\n{inputs['patent']}\n</patent>"
    else:
        body = (
            f"<patent_a>\n{inputs['patent_a']}\n</patent_a>\n\n"
            f"<patent_b>\n{inputs['patent_b']}\n</patent_b>"
        )
    extra = inputs.get("problem", "").strip() if mode != "keyword" else ""
    if extra:
        body += f"\n\n<problem>\n{extra}\n</problem>"
    return f"{header}\n\n{body}"


def _format_history(history: list[dict]) -> str:
    """최근 분석 이력을 모델용 컨텍스트 텍스트로 변환한다."""
    lines = []
    for h in history[-HISTORY_CONTEXT_LIMIT:]:
        lines.append(f"- [{h.get('date', '')}] 입력: {h.get('keywords', '')}")
        for idea in h.get("ideas", []):
            lines.append(f"  · {idea.get('title', '')} (TRL {idea.get('trl', '?')}): {idea.get('summary', '')}")
    return "<analysis_history>\n" + "\n".join(lines) + "\n</analysis_history>"


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return lo


def _normalize_result(result: dict) -> dict:
    """모델 출력의 수치 범위를 보정한다 (점수 1~10, TRL 1~9, 기여도 합 100%)."""
    for idea in result.get("ideas", []):
        scores = idea.get("scores", {})
        idea["scores"] = {k: _clamp(scores.get(k), 1, 10) for k, _ in SCORE_METRICS}
        idea["trl"] = _clamp(idea.get("trl"), 1, 9)
        contribs = [c for c in idea.get("layer_contributions", []) if c.get("percent", 0) > 0]
        total = sum(c["percent"] for c in contribs)
        if total > 0:
            for c in contribs:
                c["percent"] = round(c["percent"] * 100 / total, 1)
        idea["layer_contributions"] = contribs
    return result


def analyze_patent_data(
    mode: str,
    inputs: dict,
    history: list[dict] | None = None,
    temperature: float = 0.5,
    priority_metric: str = DEFAULT_PRIORITY,
    target_trl: str = DEFAULT_TRL,
    image: dict | None = None,
) -> dict:
    """입력을 Claude에 보내 선행기술 분석 / 개선 아이디어(정량 평가 포함)를 dict로 반환한다.

    청구항은 포함하지 않는다 → generate_claims_and_diagram()으로 아이디어별 온디맨드 생성.

    mode: "keyword" | "single" | "compare"
    inputs:
        keyword -> {"stack": str, "problem": str}
        single  -> {"patent": str, "problem": str(선택)}
        compare -> {"patent_a": str, "patent_b": str, "problem": str(선택)}
    history: 과거 분석 이력 (history.json). 주어지면 이를 회피/융합 발전한 아이디어를 요청한다.
    temperature: 아이디어 창의성 0.0~1.0. ※ API의 temperature 파라미터가 아니라 프롬프트 지시로 반영된다
        (claude-sonnet-5-5 등 현재 모델은 기본값 외의 temperature를 400 오류로 거부함).
    priority_metric: PRIORITY_OPTIONS의 키. 해당 지표 우위 아이디어 설계 + 가중 정렬.
    target_trl: TRL_OPTIONS의 키. 아이디어의 TRL 범위를 제한한다.
    image: load_image()가 반환한 구조도 이미지 {"data", "media_type"}. 주어지면 함께 판독해 분석한다.
    """
    if mode not in MODE_INSTRUCTIONS:
        raise ValueError(f"알 수 없는 모드: {mode}")

    user_message = _build_user_message(mode, inputs)
    if history:
        user_message = (
            f"{_format_history(history)}\n\n"
            "위 과거 분석 이력을 회피하거나 융합 발전시킨 완전히 새로운 차세대 아이디어를 도출하라.\n\n"
            f"{user_message}"
        )

    content: str | list = user_message
    if image:
        content = [
            _image_block(image),
            {
                "type": "text",
                "text": (
                    "첨부한 이미지는 분석 대상 소자의 스택 적층 구조도(또는 특허 도면)다. 이미지에서 층 구성·재료·두께를 "
                    "판독해 아래 텍스트 입력과 함께 분석의 근거로 사용하고, 텍스트와 이미지가 다르면 그 차이를 "
                    "prior_art.summary에 언급해.\n\n" + user_message
                ),
            },
        ]

    text = _call_claude(
        system=build_system_prompt(temperature, priority_metric, target_trl),
        user_message=content,
        max_tokens=32000,  # 텍스트 + 정량 데이터만 생성 (SVG는 온디맨드)
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": RESULT_SCHEMA},
        },
    )
    try:
        return _normalize_result(json.loads(text))
    except json.JSONDecodeError:
        raise AnalyzerError("모델 응답을 JSON으로 해석하지 못했습니다. 다시 시도해 주세요.")


SVG_SYSTEM_PROMPT = (
    f"{PERSONA} 너는 디스플레이 소자 단면도를 SVG로 정밀하게 그리는 일도 맡고 있어. "
    "입력된 개선 아이디어가 적용된 소자의 '스택 구조 단면도(Visual Stack Schema)'를 그려. "
    "단면도 하나만 봐도 구조와 개선 포인트가 이해되어야 해.\n\n"
    f"{STACK_DIAGRAM_RULES}\n\n"
    "출력은 <svg>로 시작해 </svg>로 끝나는 SVG 코드 하나만. 설명 문장이나 코드 블록 표시(```)는 붙이지 마."
)


def generate_svg_diagram(idea_text: str) -> str:
    """아이디어 설명을 받아 해당 아이디어의 SVG 단면도 코드만 생성해 반환한다 (온디맨드 호출용)."""
    return _call_claude(
        system=SVG_SYSTEM_PROMPT,
        user_message=f"<idea>\n{idea_text}\n</idea>",
        max_tokens=24000,
        output_config={"effort": "medium"},
    )


# ---------------------------------------------------------------------------
# 구조도 이미지 (멀티모달 비전 입력)
# ---------------------------------------------------------------------------
SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # Claude API 이미지 1장당 최대 5MB


def load_image(image_file) -> dict:
    """업로드된 이미지(Streamlit UploadedFile 등)를 검증해 {"data": bytes, "media_type": str}로 반환한다."""
    data = image_file.getvalue() if hasattr(image_file, "getvalue") else image_file.read()
    media_type = (getattr(image_file, "type", "") or "").lower()
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in SUPPORTED_IMAGE_TYPES:
        raise AnalyzerError("지원하지 않는 이미지 형식입니다. PNG, JPG, WEBP 파일을 올려 주세요.")
    if len(data) > MAX_IMAGE_BYTES:
        raise AnalyzerError(
            f"이미지가 너무 큽니다 ({len(data) / 1024 / 1024:.1f}MB). 5MB 이하로 줄여서 올려 주세요."
        )
    if not data:
        raise AnalyzerError("이미지 파일이 비어 있습니다.")
    return {"data": data, "media_type": media_type}


def _image_block(image: dict) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image["media_type"],
            "data": base64.standard_b64encode(image["data"]).decode("utf-8"),
        },
    }


IMAGE_SVG_PROMPT = (
    f"{PERSONA} 지금은 사용자가 올린 디스플레이 스택 적층 구조도 또는 특허 도면 이미지를 판독해, "
    "PRISM Air 표준 양식의 SVG 스택 단면도로 다시 그리는 단계야.\n\n"
    "<reading_rules>\n"
    "- 이미지에서 각 층을 아래(기판)부터 위(봉지/출광면)까지 순서대로 식별하고, 층 이름·재료·두께·도면 부호를 판독해.\n"
    "- 이미지에 적힌 글자와 수치는 그대로 옮기고, 판독이 불확실하거나 이미지에 없는 정보를 보충한 경우 라벨에 '(추정)'을 붙여.\n"
    "- 이미지에 표현된 계면, 광 경로, 화살표 의미도 최대한 살려서 옮겨.\n"
    "- 이미지가 디스플레이 적층 구조가 아니거나 판독이 불가능하면, 그 사실을 SVG 안의 안내 문구로 표시해.\n"
    "</reading_rules>\n\n"
    f"{STACK_DIAGRAM_RULES}\n\n"
    "단, 이미지에서 판독한 구조를 그대로 재현하는 것이 목적이므로 'NEW/변경' 배지는 사용자가 개선점으로 표시한 층에만 붙여. "
    "출력은 <svg>로 시작해 </svg>로 끝나는 SVG 코드 하나만. 설명 문장이나 코드 블록 표시(```)는 붙이지 마."
)


def generate_svg_from_image(image_file, note: str = "") -> str:
    """업로드된 디스플레이 구조도 이미지를 Claude 비전으로 판독해 PRISM Air 스타일 SVG 단면도 코드를 생성한다.

    image_file: Streamlit UploadedFile (또는 getvalue()/read()와 type 속성을 가진 파일 객체)
    note: 판독에 참고할 사용자 입력(스택 텍스트 등). 선택.
    """
    image = load_image(image_file)
    text = "첨부한 구조도 이미지를 판독해 SVG 단면도로 그려 줘."
    if note.strip():
        text += f"\n\n<user_note>\n{note.strip()}\n</user_note>"
    return _call_claude(
        system=IMAGE_SVG_PROMPT,
        user_message=[_image_block(image), {"type": "text", "text": text}],
        max_tokens=24000,
        output_config={"effort": "medium"},
    )


# ---------------------------------------------------------------------------
# 아이디어 심층 분석 3종 (온디맨드)
# ---------------------------------------------------------------------------
DEEP_ANALYSIS_EFFORT = "high"  # 비용을 줄이려면 "medium"으로 낮출 수 있음

_DEEP_COMMON = (
    "출력은 Streamlit에 그대로 렌더링되는 한국어 마크다운으로 작성해. 맨 위에 문서 제목(#)은 쓰지 말고 "
    "'### ' 소제목부터 시작해. HTML·코드 블록은 쓰지 말고, 비교가 필요한 곳은 마크다운 표를 사용해. "
    "수치(두께 nm, 굴절률, OD, 공정 온도 ℃, 마스크 수, 비용 비율 등)는 근거와 함께 구체적으로 제시하고, "
    "추정치는 '(추정)'으로 표시해."
)

DESIGN_AROUND_PROMPT = (
    f"{PERSONA} 지금은 선택된 아이디어를 경쟁사 특허로부터 자유롭게 실시(FTO)하기 위한 회피 설계(Design-Around) 단계야.\n\n"
    "<task>\n"
    "### 1. 경쟁사 예상 독립항 구성요소 분해\n"
    "- 이 기술 영역에서 경쟁사가 보유했을 법한 대표 독립항을 구성요소(A, B, C, D…) 단위로 분해한 표.\n"
    "- 입력에 실제 특허 내용(선행기술 요약)이 있으면 그것을 우선 사용하고, 없으면 '가상의 대표 독립항'임을 명시해. "
    "특허 번호·출원인·청구항 문구를 지어내지 마.\n"
    "### 2. 구성요소 완비 원칙(All-Elements Rule) 기반 회피 포인트\n"
    "- 어떤 구성요소를 삭제·치환·순서 변경·분리·통합하면 문언 침해를 벗어나는지 표로 정리.\n"
    "### 3. 구체적 스택 변형안 2~3개\n"
    "- 변형안마다 변경 전/후 적층 순서, 재료, 두께, 변경된 구성요소, 성능 영향(반사율·출광·신뢰성)을 제시.\n"
    "### 4. 균등론 침해 리스크 평가\n"
    "- 각 변형안의 기능·방법·효과 동일성 관점 리스크(상/중/하)와 근거.\n"
    "### 5. 권장 회피 전략 및 후속 조치\n"
    "- 최적 변형안, 추가 확보할 방어 특허 포인트, 실제 FTO 검토 시 확인할 사항.\n"
    "</task>\n\n"
    f"{_DEEP_COMMON} 마지막 줄에 '※ 본 분석은 AI 기반 사전 검토이며 정식 FTO 의견은 변리사 검토가 필요합니다.'를 적어."
)

TRIZ_PROMPT = (
    f"{PERSONA} 지금은 선택된 아이디어의 기술적 모순을 TRIZ로 격파하는 단계야. "
    "디스플레이 광학(반사·출광·도파·간섭)과 소재 물리(굴절률, 흡수, 열·수분 안정성) 관점에서 분석해.\n\n"
    "<task>\n"
    "### 1. 기술적 모순 정의\n"
    "- 개선하려는 파라미터와 악화되는 파라미터를 TRIZ 39 공학 파라미터에 매핑한 표 (예: 반사율 저감 ↔ 휘도/출광 효율).\n"
    "- 물리적 모순(같은 층이 A이면서 동시에 not-A여야 하는 조건)이 있으면 함께 정의.\n"
    "### 2. 모순 행렬 기반 추천 발명 원리\n"
    "- 모순 행렬에서 도출되는 40가지 발명 원리 후보를 번호와 이름으로 제시하고 선택 이유를 설명.\n"
    "### 3. 발명 원리 적용 방안 (3개 이상)\n"
    "- 원리마다 디스플레이 스택에 적용하는 구체적 구조·재료·공정 변경안과 예상 수치 효과.\n"
    "### 4. 물리적 모순 분리 원리\n"
    "- 시간·공간·조건·전체와 부분 분리 중 해당되는 방법과 적용안.\n"
    "### 5. 이상적 최종 결과(IFR)와 종합 평가\n"
    "- IFR 정의, 적용안별 모순 해소 정도·구현 난이도 비교표, 최종 권장안.\n"
    "</task>\n\n"
    f"{_DEEP_COMMON}"
)

FAB_MARGIN_PROMPT = (
    f"{PERSONA} 너는 OLED 양산 라인(증착·봉지·포토 공정)의 공정 통합 책임자 역할도 맡고 있어. "
    "지금은 선택된 아이디어의 양산 적용성을 검증하는 단계야.\n\n"
    "<task>\n"
    "### 1. 공정 흐름(Process Flow) 및 변경점\n"
    "- 기존 대비 추가·변경되는 공정 단계를 순서대로 표로 정리 (공정명, 장비, 주요 조건, 신규/변경 여부).\n"
    "### 2. 인라인 증착 및 포토 공정 호환성\n"
    "- FMM/오픈마스크 증착, TFE(CVD/잉크젯) 위 저온 포토 공정(유기층 손상 한계 온도 약 100℃ 이하), "
    "현상액·스트리퍼에 의한 하부층 손상, 얼라인 정밀도, 아웃가싱 등 호환성 체크 표 (항목, 판정 ○/△/×, 근거).\n"
    "### 3. 양산 리스크 FMEA\n"
    "- 고장 모드, 영향, 원인, 심각도(S)·발생도(O)·검출도(D) 1~10, RPN, 대응책을 표로.\n"
    "### 4. 비용 효율성 분석\n"
    "- 추가 마스크 수, 재료비, TACT 영향, 장비 투자, 수율 영향을 정리하고 패널당 원가 증감을 추정.\n"
    "### 5. 공정 마진 확보 방안 및 Go/No-Go 판정\n"
    "- 공정 윈도우를 넓히는 설계·공정 대책, 파일럿 검증 항목(DOE), 최종 판정과 조건.\n"
    "</task>\n\n"
    f"{_DEEP_COMMON}"
)

# 화면/저장용 분석 종류 정의: kind → (버튼·제목 라벨, 시스템 프롬프트)
DEEP_ANALYSES = {
    "design_around": ("🛡️ 경쟁사 특허 회피 분석", DESIGN_AROUND_PROMPT),
    "triz": ("⚙️ TRIZ 모순 극복 분석", TRIZ_PROMPT),
    "fab_margin": ("🏭 양산성 공정 마진 검증", FAB_MARGIN_PROMPT),
}


def _deep_analysis(kind: str, idea_text: str) -> str:
    return _call_claude(
        system=DEEP_ANALYSES[kind][1],
        user_message=f"<idea>\n{idea_text}\n</idea>",
        max_tokens=16000,
        output_config={"effort": DEEP_ANALYSIS_EFFORT},
    ).strip()


def generate_design_around_analysis(idea_text: str) -> str:
    """경쟁사 특허 독립항 권리범위를 우회하기 위한 스택 변형 및 회피 전략 (마크다운)."""
    return _deep_analysis("design_around", idea_text)


def generate_triz_analysis(idea_text: str) -> str:
    """광학/소재 물리계의 기술적 모순을 TRIZ 40 발명 원리로 격파하는 적용 방안 (마크다운)."""
    return _deep_analysis("triz", idea_text)


def generate_fab_margin_analysis(idea_text: str) -> str:
    """인라인 증착·포토 공정 호환성, 양산 리스크, 비용 효율성 검증 (마크다운)."""
    return _deep_analysis("fab_margin", idea_text)


# ---------------------------------------------------------------------------
# 청구항 + 도면 온디맨드 고도화 (Maturation)
# ---------------------------------------------------------------------------
CLAIMS_SYSTEM_PROMPT = (
    f"{PERSONA} 지금은 선택된 개선 아이디어 하나를 특허 출원 가능한 수준으로 고도화하는 단계야. "
    "아이디어를 바탕으로 1)한국 특허 명세서 형식의 청구범위와 2)대표 도면(스택 구조 단면도)을 함께 작성해.\n\n"
    "<claim_rules>\n"
    "- 독립항 1~2개(소자 구조 독립항 필수, 필요 시 제조 방법 독립항)와 종속항 6~10개를 작성.\n"
    "- 독립항은 '~을 포함하는 표시 장치'처럼 구성요소와 그 결합 관계를 명확히 기재하고, "
    "발명의 핵심 특징을 '~를 특징으로 하는' 형태로 마무리.\n"
    "- 종속항은 '제1항에 있어서,'로 시작해 재료, 두께·굴절률·OD 등 수치 범위, 배치 관계를 단계적으로 한정.\n"
    "- 선행기술과 차별되는 구성요소가 독립항에 반드시 포함되도록 할 것. 지나치게 넓어 신규성이 없거나, "
    "지나치게 좁아 회피가 쉬운 청구항은 피할 것.\n"
    "- 청구항 본문에는 도면 부호를 쓰지 말고, reference_numerals에 도면 부호(100, 110, 120 …)와 "
    "구성요소 명칭을 정리.\n"
    "</claim_rules>\n\n"
    "도면(svg)은 아래 규칙을 따르되, 각 층 라벨에 reference_numerals의 도면 부호를 함께 표기해 "
    "청구항 구성요소와 도면이 대응되게 해. 상단 제목은 '【도 1】 <아이디어 이름>'.\n\n"
    f"{STACK_DIAGRAM_RULES}\n\n"
    "모든 텍스트는 한국어로 작성해."
)

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "claim_type": {"type": "string", "description": "'독립항' 또는 '종속항'"},
                    "text": {"type": "string"},
                },
                "required": ["number", "claim_type", "text"],
                "additionalProperties": False,
            },
        },
        "reference_numerals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"numeral": {"type": "string"}, "name": {"type": "string"}},
                "required": ["numeral", "name"],
                "additionalProperties": False,
            },
        },
        "svg": {"type": "string", "description": "stack_diagram_rules를 따르는 완결된 <svg>...</svg> 도면"},
    },
    "required": ["claims", "reference_numerals", "svg"],
    "additionalProperties": False,
}


def generate_claims_and_diagram(selected_idea_text: str) -> dict:
    """선택된 아이디어 텍스트로 청구항(독립항/종속항)과 SVG 스택 도면을 한 번에 생성한다.

    반환: {"claims": [{"number", "claim_type", "text"}], "reference_numerals": [{"numeral", "name"}], "svg": str}
    """
    text = _call_claude(
        system=CLAIMS_SYSTEM_PROMPT,
        user_message=f"<idea>\n{selected_idea_text}\n</idea>",
        max_tokens=32000,
        output_config={
            "effort": "high",  # 청구항은 권리범위 품질이 중요하므로 high 유지
            "format": {"type": "json_schema", "schema": CLAIMS_SCHEMA},
        },
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise AnalyzerError("청구항 생성 응답을 JSON으로 해석하지 못했습니다. 다시 시도해 주세요.")
    data["claims"] = sorted(data.get("claims", []), key=lambda c: c.get("number", 0))
    return data


# ---------------------------------------------------------------------------
# Word(.docx) 리포트
# ---------------------------------------------------------------------------
REPORT_FONT = "Malgun Gothic"  # 맑은 고딕


def _svg_to_png(svg_code: str, width_px: int = 1800) -> bytes | None:
    """SVG를 PNG로 변환 (Word는 python-docx로 SVG를 직접 넣을 수 없음). 실패 시 None."""
    try:
        import resvg_py

        png = resvg_py.svg_to_bytes(
            svg_string=svg_code,
            width=width_px,
            background="#ffffff",
            font_family=REPORT_FONT,
            sans_serif_family=REPORT_FONT,
        )
        return bytes(png)
    except Exception:  # 변환 실패해도 리포트 자체는 만들어지도록
        return None


def new_report_doc(cover_title: str, subtitle: str = ""):
    """PRISM Air 공통 서식(맑은 고딕, 여백, 표지 문구)이 적용된 Word 문서를 만든다."""
    from datetime import datetime

    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = Cm(2.5)
        section.left_margin = section.right_margin = Cm(2.5)

    # 한글 글꼴 적용 (eastAsia 글꼴을 따로 지정해야 한글에도 반영됨)
    for style_name in ["Normal", "Title", "Heading 1", "Heading 2", "Heading 3"]:
        style = doc.styles[style_name]
        style.font.name = REPORT_FONT
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), REPORT_FONT)
    doc.styles["Normal"].font.size = Pt(10.5)
    doc.styles["Normal"].paragraph_format.line_spacing = 1.4

    t = doc.add_heading(cover_title, level=0)
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(f"PRISM Air · {subtitle + ' · ' if subtitle else ''}생성일 {datetime.now():%Y-%m-%d %H:%M}")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = note.add_run("※ AI가 생성한 초안입니다. 출원 전 반드시 변리사의 검토를 거치십시오.")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0xC6, 0x28, 0x28)
    return doc


def add_report_heading(doc, text: str, level: int = 1):
    from docx.shared import RGBColor

    h = doc.add_heading(text, level=level)
    for run in h.runs:
        run.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
    return h


def generate_docx_report(
    title: str,
    claims_text: str,
    svg_code: str,
    *,
    summary: str = "",
    reference_numerals: list[dict] | None = None,
) -> bytes:
    """청구항과 도면을 담은 특허 명세서 초안을 Word(.docx) 바이너리로 생성한다.

    claims_text: 청구항 전체 텍스트. '【청구항 N】'으로 시작하는 줄은 소제목으로 강조된다.
    svg_code: 대표 도면 SVG (PNG로 변환해 삽입).
    """
    import io

    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    doc = new_report_doc("특허 명세서 초안")

    def heading(text: str):
        return add_report_heading(doc, text)

    heading("【발명의 명칭】")
    doc.add_paragraph(title)

    if summary:
        heading("【발명의 개요】")
        doc.add_paragraph(summary)

    heading("【청구범위】")
    for line in claims_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        p = doc.add_paragraph()
        if line.startswith("【청구항"):
            p.paragraph_format.space_before = Pt(8)
            p.add_run(line).bold = True
        else:
            p.add_run(line)
            p.paragraph_format.first_line_indent = Cm(0.5)

    heading("【도면】")
    png = _svg_to_png(svg_code) if svg_code else None
    if png:
        doc.add_picture(io.BytesIO(png), width=Cm(16))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        cap = doc.add_paragraph("【도 1】 스택 구조 단면도")
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        doc.add_paragraph("(도면을 이미지로 변환하지 못했습니다. 대시보드의 SVG 원본 코드를 참고하십시오.)")

    if reference_numerals:
        heading("【부호의 설명】")
        table = doc.add_table(rows=1, cols=2)
        table.style = "Light Grid Accent 1"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.rows[0].cells[0].text = "부호"
        table.rows[0].cells[1].text = "구성요소"
        for r in reference_numerals:
            cells = table.add_row().cells
            cells[0].text = str(r.get("numeral", ""))
            cells[1].text = str(r.get("name", ""))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _call_claude(system: str, user_message: str | list, max_tokens: int, output_config: dict) -> str:
    """Claude를 스트리밍으로 호출해 응답 텍스트를 반환한다. 오류는 AnalyzerError로 변환.

    user_message: 텍스트, 또는 이미지가 포함된 content 블록 리스트.
    """
    client = _get_client()
    model = os.getenv("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL
    try:
        # 출력이 길 수 있으므로 스트리밍으로 받아 타임아웃을 피한다.
        with client.beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
            output_config=output_config,
            # 안전 분류기가 오탐으로 거절할 경우 서버 측에서 다른 모델로 자동 재시도
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise AnalyzerError("API 키가 올바르지 않습니다. .env의 ANTHROPIC_API_KEY를 확인해 주세요.")
    except anthropic.NotFoundError:
        raise AnalyzerError(f"모델 '{model}'을(를) 찾을 수 없습니다. .env의 ANTHROPIC_MODEL을 확인해 주세요.")
    except anthropic.RateLimitError:
        raise AnalyzerError("요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.")
    except anthropic.BadRequestError as e:
        raise AnalyzerError(f"잘못된 요청입니다: {e.message}")
    except anthropic.APIStatusError as e:
        raise AnalyzerError(f"API 오류 ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise AnalyzerError("네트워크 연결에 실패했습니다. 인터넷 연결을 확인해 주세요.")

    if response.stop_reason == "refusal":
        raise AnalyzerError("모델이 이 요청에 대한 응답을 거절했습니다. 입력 내용을 확인해 주세요.")
    if response.stop_reason == "max_tokens":
        raise AnalyzerError("응답이 너무 길어 중간에 잘렸습니다. 입력을 줄여 다시 시도해 주세요.")

    return "".join(b.text for b in response.content if b.type == "text")
