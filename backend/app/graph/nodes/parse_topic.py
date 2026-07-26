"""议题分解节点：判断议题类别 + 提取关键变量。

输入：state["topic"] + state["user_background"] + state["topic_category"]
输出：state["category"] + state["briefing"]
"""
from pydantic import BaseModel, Field

from app.llm import get_structured_llm


VALID_CATEGORIES = {
    "real_estate", "gold", "offer", "job_change",
    "investment", "entrepreneurship",
    "stocks", "buy_bag",  # P0-S1 补：前端已加但后端白名单漏了
    "general",
}

CATEGORY_VARIABLES: dict[str, list[str]] = {
    "real_estate": ["总预算与首付", "持有期限", "租售成本", "现金流安全垫"],
    "gold": ["配置目标", "持有期限", "波动承受力", "替代资产"],
    "offer": ["现金与股权", "成长空间", "工作强度", "机会成本", "可逆性"],
    "job_change": ["收入变化", "能力迁移", "发展空间", "风险承受力", "退路"],
    "investment": ["预期收益", "最大损失", "流动性", "持有期限", "集中度"],
    "entrepreneurship": ["需求验证", "现金流", "获客成本", "团队", "退出路径"],
    "stocks": ["估值", "基本面", "最大回撤", "仓位", "持有期限"],
    "buy_bag": ["预算占比", "使用频率", "替代选择", "情绪价值"],
    "general": ["目标", "现实约束", "替代方案", "最担心的结果"],
}

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("real_estate", ("买房", "购房", "置业", "房产")),
    ("gold", ("黄金", "贵金属")),
    ("offer", ("offer", "入职", "录用")),
    ("entrepreneurship", ("创业", "开公司", "startup")),
    ("job_change", ("跳槽", "转行", "换工作", "辞职")),
    ("stocks", ("股票", "个股")),
    ("buy_bag", ("买包", "包包")),
    ("investment", ("投资", "基金", "理财")),
)


class TopicBriefing(BaseModel):
    category: str = Field(description="议题类别")
    key_variables: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(description="一句话议题摘要")
    options: list[str] = Field(default_factory=list, max_length=5)
    user_facts: list[str] = Field(default_factory=list, max_length=12)
    preferences: list[str] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=8)
    research_questions: list[str] = Field(default_factory=list, max_length=6)


PARSE_PROMPT = """你是邪杠议会主调度。分析用户提出的决策议题。

【用户议题】{topic}
【用户背景】{background}
【已有类别提示】{category_hint}

请输出 JSON：
{{
  "category": "real_estate|gold|offer|job_change|investment|entrepreneurship|general",
  "key_variables": ["变量1", "变量2", ...],
  "summary": "一句话概括",
  "options": ["明确的备选方案"],
  "user_facts": ["只写用户明确提供的信息"],
  "preferences": ["用户明确表达的偏好或价值排序"],
  "unknowns": ["会改变结论但用户尚未提供的信息"],
  "research_questions": ["确实需要外部核实的问题"]
}}

【固定议题清单】（命中即归类，否则 general）：
- real_estate: 买房、购房、置业、房产
- gold: 买黄金、配置黄金、贵金属
- offer: 接 offer、接 job、入职
- job_change: 跳槽、转行、换工作
- investment: 投资、买股票、买基金
- entrepreneurship: 创业、开公司、startup

提取 3-7 个关键变量。如果类别不明确，归为 general。

直接输出 JSON，不要其他文字。"""


def parse_topic(state: dict) -> dict:
    """LangGraph 节点函数：分析议题 + 生成简报。"""
    category_hint = state.get("topic_category")
    if category_hint in VALID_CATEGORIES and category_hint != "general":
        return _fallback_briefing(
            state, category_hint, classification_degraded=False
        )

    structured = get_structured_llm(TopicBriefing, temperature=0.3)
    prompt = PARSE_PROMPT.format(
        topic=state["topic"],
        background=state.get("user_background") or "（无）",
        category_hint=state.get("topic_category") or "（无）",
    )
    try:
        result: TopicBriefing = structured.invoke(prompt)
    except Exception:
        return _fallback_briefing(state, category_hint)

    # 后置校验：LLM 偶尔会乱填 category，强转 general
    category = result.category if result.category in VALID_CATEGORIES else "general"

    decision_model = {
        "question": state["topic"],
        "options": list(getattr(result, "options", []) or []),
        "user_facts": list(getattr(result, "user_facts", []) or []),
        "preferences": list(getattr(result, "preferences", []) or []),
        "unknowns": list(getattr(result, "unknowns", []) or []),
        "research_questions": list(
            getattr(result, "research_questions", []) or []
        ),
    }
    return {
        "category": category,
        "briefing": {
            "key_variables": result.key_variables,
            "summary": result.summary,
        },
        "decision_model": decision_model,
    }


def _fallback_briefing(
    state: dict,
    category_hint: str | None = None,
    *,
    classification_degraded: bool = True,
) -> dict:
    """Keep the council usable when topic classification is unavailable."""
    topic = str(state.get("topic") or "待分析决策").strip()
    category = category_hint if category_hint in VALID_CATEGORIES else None
    if not category:
        lowered = topic.lower()
        category = next(
            (
                candidate
                for candidate, keywords in CATEGORY_KEYWORDS
                if any(keyword in lowered for keyword in keywords)
            ),
            "general",
        )
    background = str(state.get("user_background") or "").strip()
    variables = list(CATEGORY_VARIABLES[category])
    return {
        "category": category,
        "briefing": {
            "key_variables": variables,
            "summary": topic,
            "classification_degraded": classification_degraded,
        },
        "decision_model": {
            "question": topic,
            "options": [],
            "user_facts": [background] if background else [],
            "preferences": [],
            "unknowns": variables[:4],
            "research_questions": [
                f"核实{variable}的当前情况"
                for variable in variables[:3]
            ],
        },
    }
