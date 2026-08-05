"""决策路径相关 pydantic schema - PRD §13"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ============ L0: 基础数据结构 ============

DecisionQuestionType = Literal[
    "single_choice",
    "multi_choice",
    "range_choice",
    "choice_with_custom",
    "search_select",
    "number",
    "short_text",
]


class DecisionQuestionOption(BaseModel):
    value: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=120)
    description: str | None = None


class DecisionQuestion(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    question: str = Field(min_length=4, max_length=180)
    why_it_matters: str = Field(min_length=4, max_length=240)
    answer_type: DecisionQuestionType
    options: list[DecisionQuestionOption] = Field(default_factory=list, max_length=8)
    allow_custom: bool = False
    custom_placeholder: str | None = None
    unknown_assumption: str = ""


class DecisionBrief(BaseModel):
    """任务简报(PRD §4.2 / v1.1 §6.8)。

    注意(PRD v1.1 修订):不再包含 autonomous_research,
    工具选择与调研项由专家 Agent 在运行时自主决定。
    """

    decision_goal: str
    decision_options: list[str]
    analysis_dimensions: list[str]
    final_deliverables: list[str]


class AgentResearchPolicy(BaseModel):
    """调研策略声明(PRD v1.1 §6.8 / §13.2)。

    会前弹窗用,统一只告诉用户"公开信息将由专家在会议中自主判断",
    不展示具体搜索项、工具顺序或次数。
    """

    mode: Literal["runtime_autonomous"] = "runtime_autonomous"
    public_message: str = (
        "公开信息将由专家在会议中自主判断并按需核实，实际搜索与计算会在发生后展示。"
    )


class ExtractedFact(BaseModel):
    """从用户原文里识别出来的事实。"""

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=64)
    value: str
    source: str = "user_background"
    editable: bool = True


# ============ L1: LLM 结构化输出 ============

class LLMExtractedFactLLM(BaseModel):
    """LLM 提取事实时的紧凑结构"""

    id: str
    label: str
    value: str


class LLMDecisionOutput(BaseModel):
    """LLM 输出的最终结构(强校验)。"""

    decision_goal: str = Field(min_length=4, max_length=400)
    decision_options: list[str] = Field(min_length=2, max_length=6)
    analysis_dimensions: list[str] = Field(min_length=2, max_length=8)
    final_deliverables: list[str] = Field(min_length=2, max_length=10)
    extracted_facts: list[LLMExtractedFactLLM] = Field(default_factory=list, max_length=8)
    selected_question_ids: list[str] = Field(default_factory=list, max_length=3)


# ============ L2: API 输入 / 输出 ============

class DecisionPathPreviewRequest(BaseModel):
    """POST /api/decision-path/preview 请求体"""

    topic: str = Field(min_length=1, max_length=500)
    topic_category: str | None = None
    user_background: str | None = Field(default=None, max_length=4000)
    selected_expert_ids: list[str] | None = None
    generation_id: str | None = Field(default=None, max_length=128)
    brief_feedback: str | None = Field(default=None, max_length=600)
    fact_edits: list[dict[str, Any]] = Field(default_factory=list)
    existing_answers: list[dict[str, Any]] = Field(default_factory=list)


class Assumption(BaseModel):
    """保守假设,PRD §4.4"""

    question_id: str
    assumption_text: str
    conclusion_flip_condition: str = ""


class DecisionPathPreviewResponse(BaseModel):
    version: str = "1.0"
    generation_id: str
    category: str
    agent_research_policy: AgentResearchPolicy = Field(default_factory=AgentResearchPolicy)
    brief: DecisionBrief
    extracted_facts: list[ExtractedFact] = Field(default_factory=list)
    questions: list[DecisionQuestion] = Field(default_factory=list, max_length=3)
    assumptions: list[Assumption] = Field(default_factory=list)
    fallback_used: bool = False


class AnswerItem(BaseModel):
    question_id: str
    value: str | list[str]
    custom_value: str | None = None


class DecisionPathConfirmPayload(BaseModel):
    """POST /api/decision-path/{id}/confirm 时客户端给的最终答案(bundle)。"""

    decision_path_generation_id: str
    brief_confirmation: Literal["confirmed"] = "confirmed"
    decision_path_answers: list[AnswerItem]
    fact_edits: list[dict[str, Any]] = Field(default_factory=list)


class CreateParliamentWithDecisionPathRequest(BaseModel):
    """扩展后的 createParliament: 带 decision_path bundle"""

    topic: str = Field(min_length=1, max_length=500)
    topic_category: str | None = None
    user_background: str | None = Field(default=None, max_length=4000)
    user_id: str | None = None
    selected_expert_ids: list[str] | None = None
    idempotency_key: str | None = Field(default=None, max_length=128)
    decision_path_generation_id: str | None = Field(default=None, max_length=128)
    brief_confirmation: str | None = None
    decision_path_answers: list[AnswerItem] = Field(default_factory=list)


class DecisionPathDraft(BaseModel):
    """从草稿表读出来的 payload。"""

    version: str
    category: str
    brief: DecisionBrief
    extracted_facts: list[ExtractedFact]
    questions: list[DecisionQuestion]
    assumptions: list[Assumption]
