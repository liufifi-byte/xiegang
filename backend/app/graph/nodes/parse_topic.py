"""议题分解节点：判断议题类别 + 提取关键变量。

输入：state["topic"] + state["user_background"] + state["topic_category"]
输出：state["category"] + state["briefing"]
"""
from pydantic import BaseModel, Field

from app.llm import get_structured_llm


VALID_CATEGORIES = {
    "real_estate", "gold", "offer", "job_change",
    "investment", "entrepreneurship", "general",
}


class TopicBriefing(BaseModel):
    category: str = Field(description="议题类别")
    key_variables: list[str] = Field(min_length=1, max_length=10)
    summary: str = Field(description="一句话议题摘要")


PARSE_PROMPT = """你是邪杠议会主调度。分析用户提出的决策议题。

【用户议题】{topic}
【用户背景】{background}
【已有类别提示】{category_hint}

请输出 JSON：
{{
  "category": "real_estate|gold|offer|job_change|investment|entrepreneurship|general",
  "key_variables": ["变量1", "变量2", ...],
  "summary": "一句话概括"
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
    structured = get_structured_llm(TopicBriefing, temperature=0.3)
    prompt = PARSE_PROMPT.format(
        topic=state["topic"],
        background=state.get("user_background") or "（无）",
        category_hint=state.get("topic_category") or "（无）",
    )
    result: TopicBriefing = structured.invoke(prompt)

    # 后置校验：LLM 偶尔会乱填 category，强转 general
    category = result.category if result.category in VALID_CATEGORIES else "general"

    return {
        "category": category,
        "briefing": {
            "key_variables": result.key_variables,
            "summary": result.summary,
        },
    }
