"""One-shot information gate before professional research begins."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from app.db import get_supabase
from app.event_store import append_event
from app.llm import get_structured_llm
from app.llm_concurrency import get_llm_semaphore


class ClarificationQuestion(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    question: str = Field(min_length=4, max_length=180)
    why_it_matters: str = Field(min_length=4, max_length=220)
    answer_type: Literal["single_choice", "number", "short_text"]
    options: list[str] = Field(default_factory=list, max_length=6)
    impact: Literal["high"] = "high"
    source: Literal["user_only"] = "user_only"


class ClarificationDecision(BaseModel):
    needs_clarification: bool
    reason: str = Field(default="", max_length=300)
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=3)


CLARIFICATION_PROMPT = """你是 Decision Council 的信息门槛 Agent。

你的唯一任务：判断专家开始调研前，是否缺少只有用户本人知道、并且很可能改变最终建议的事实。

【决策问题】
{topic}

【用户背景】
{background}

【决策模型】
{decision_model}

规则：
1. 最多提出 2 个问题；能不问就不问，0 个问题是最好的结果。只有缺失该信息会导致建议方向反转时才问。
2. 只问只有用户能回答的信息，例如预算占比、应急金、持有期、真正目标、家庭意见、退出条件。
3. 当前价格、政策、市场数据、公开人物或历史事实应交给 Web Search，绝不能询问用户。
4. 不重复用户已经明确提供的信息。
5. 不问“风险偏好是什么”“请详细介绍情况”“还有什么补充”等空泛问题。
6. 每个问题只问一件事，并说明它为什么可能改变结论。
7. 单选题提供 2-6 个互斥选项，并包含“不确定”；数字或短文本题不提供 options。
8. 只有 impact=high 且 source=user_only 的问题才允许输出。

返回严格结构化结果。"""


_VAGUE_PATTERNS = (
    "详细介绍",
    "风险偏好是什么",
    "还有什么",
    "其他情况",
    "补充一下",
)
_PUBLIC_FACT_PATTERNS = (
    "当前价格",
    "最新价格",
    "市场价格",
    "最新政策",
    "利率是多少",
    "税率是多少",
    "历史数据",
    "市场走势",
    "公开数据",
)


def _clean_questions(
    questions: list[ClarificationQuestion],
) -> list[ClarificationQuestion]:
    cleaned: list[ClarificationQuestion] = []
    seen_ids: set[str] = set()
    seen_text: set[str] = set()
    for question in questions:
        text = re.sub(r"\s+", " ", question.question).strip()
        normalized = re.sub(r"[？?，,。.\s]", "", text)
        if not text or question.id in seen_ids or normalized in seen_text:
            continue
        if any(pattern in text for pattern in _VAGUE_PATTERNS):
            continue
        if any(pattern in text for pattern in _PUBLIC_FACT_PATTERNS):
            continue
        options = [str(option).strip() for option in question.options if str(option).strip()]
        if question.answer_type == "single_choice":
            if len(options) < 2:
                continue
            if not any("不确定" in option or "不知道" in option for option in options):
                options.append("不确定")
            options = options[:6]
        else:
            options = []
        cleaned.append(
            question.model_copy(
                update={"question": text, "options": options}
            )
        )
        seen_ids.add(question.id)
        seen_text.add(normalized)
        if len(cleaned) >= 3:
            break
    return cleaned


def _fallback_questions(state: dict) -> list[ClarificationQuestion]:
    """Fast category fallback when the lightweight model is unavailable."""
    background = " ".join(
        (
            str(state.get("topic") or ""),
            str(state.get("user_background") or ""),
        )
    ).lower()
    category = state.get("category") or state.get("topic_category") or "general"
    candidates: list[tuple[tuple[str, ...], ClarificationQuestion]] = []

    if category in {"gold", "investment", "stocks"}:
        candidates = [
            (
                ("占", "%", "全部资金", "可支配资金", "流动资金"),
                ClarificationQuestion(
                    id="allocation_share",
                    question="这笔投入大约占你全部可支配资金的多少？",
                    why_it_matters="资金占比决定价格下跌是否会影响应急能力和正常生活。",
                    answer_type="single_choice",
                    options=["不到20%", "20%–40%", "40%–60%", "60%以上", "不确定"],
                ),
            ),
            (
                ("持有", "长期", "短期", "不会使用", "闲置"),
                ClarificationQuestion(
                    id="holding_period",
                    question="这笔钱预计多久不会使用？",
                    why_it_matters="持有期限会改变你对短期回撤和退出成本的承受能力。",
                    answer_type="single_choice",
                    options=["1年以内", "1–3年", "3–5年", "5年以上", "不确定"],
                ),
            ),
            (
                ("避险", "保值", "投机", "收益", "配置"),
                ClarificationQuestion(
                    id="investment_goal",
                    question="你购买它最主要想解决什么问题？",
                    why_it_matters="保值、短期获利和资产分散对应完全不同的判断标准。",
                    answer_type="single_choice",
                    options=["资产保值", "分散风险", "短期获利", "长期增值", "不确定"],
                ),
            ),
        ]
    elif category == "real_estate":
        candidates = [
            (
                ("预算", "总价", "首付"),
                ClarificationQuestion(
                    id="housing_budget",
                    question="你的总预算和可用于首付的金额大约是多少？",
                    why_it_matters="它决定贷款压力、现金安全垫和可选房源范围。",
                    answer_type="short_text",
                ),
            ),
            (
                ("长期", "持有", "自住", "居住"),
                ClarificationQuestion(
                    id="housing_horizon",
                    question="你预计会在这套房中居住或持有多久？",
                    why_it_matters="持有期会显著影响交易成本和买房相对租房的结果。",
                    answer_type="single_choice",
                    options=["3年以内", "3–5年", "5–10年", "10年以上", "不确定"],
                ),
            ),
            (
                ("配偶", "伴侣", "父母", "家人", "家庭"),
                ClarificationQuestion(
                    id="family_alignment",
                    question="需要共同承担这项决定的家人是否支持？",
                    why_it_matters="家庭分歧会直接影响资金安排和长期执行。",
                    answer_type="single_choice",
                    options=["一致支持", "基本支持", "存在分歧", "尚未讨论", "不确定"],
                ),
            ),
        ]
    elif category in {"offer", "job_change"}:
        candidates = [
            (
                ("薪资", "工资", "收入", "涨幅"),
                ClarificationQuestion(
                    id="income_change",
                    question="新选择会让你的稳定收入发生多大变化？",
                    why_it_matters="收入变化决定机会成本和风险承受空间。",
                    answer_type="short_text",
                ),
            ),
            (
                ("目标", "成长", "管理", "技术", "转行"),
                ClarificationQuestion(
                    id="career_goal",
                    question="你希望这次变化最主要解决什么职业问题？",
                    why_it_matters="收入、成长和身份转换会导向不同的选择。",
                    answer_type="short_text",
                ),
            ),
            (
                ("退路", "回去", "可逆", "试用"),
                ClarificationQuestion(
                    id="reversibility",
                    question="如果新选择不合适，你有什么现实退路？",
                    why_it_matters="可逆性会显著改变可以承担的风险水平。",
                    answer_type="short_text",
                ),
            ),
        ]
    else:
        candidates = [
            (
                ("目标", "希望", "为了", "解决"),
                ClarificationQuestion(
                    id="primary_goal",
                    question="你最希望这次决定解决什么问题？",
                    why_it_matters="不同目标会改变专家衡量收益和代价的标准。",
                    answer_type="short_text",
                ),
            ),
            (
                ("截止", "最晚", "必须决定", "多久决定", "日期"),
                ClarificationQuestion(
                    id="decision_deadline",
                    question="你最晚需要在什么时候作出决定？",
                    why_it_matters="时间压力会改变可调查范围和替代方案。",
                    answer_type="short_text",
                ),
            ),
            (
                ("最坏", "担心", "不能接受", "底线"),
                ClarificationQuestion(
                    id="unacceptable_outcome",
                    question="哪一种结果是你最不能接受的？",
                    why_it_matters="不可接受的结果决定风险底线。",
                    answer_type="short_text",
                ),
            ),
        ]
    return [
        question
        for known_markers, question in candidates
        if not any(marker in background for marker in known_markers)
    ][:3]


async def assess_clarification_need(state: dict) -> dict:
    """Return zero to three high-impact user-only questions."""
    existing_status = state.get("clarification_status") or "not_checked"
    if existing_status in {"not_needed", "answered", "skipped"}:
        return {
            "clarification_status": existing_status,
            "clarification_questions": list(
                state.get("clarification_questions") or []
            ),
            "clarification_reason": "本场信息门槛已经处理",
        }

    # P0 修复：会前两步流（decision path setup）已确认过的议会不再追问。
    # 之前 bug：setup 弹窗收集的答案写入了 parliaments.decision_path
    # （decision_path_status=confirmed），worker 也把它塞进了 state，
    # 但本节点从不读它——LLM 又生成一批内容重复的 user_only 问题，
    # 用户在 setup 两步流之后第三次看到同题弹窗。
    if state.get("decision_path_status") == "confirmed" and state.get(
        "decision_path"
    ):
        return {
            "clarification_status": "not_needed",
            "clarification_questions": [],
            "clarification_reason": "决策路径两步流已在会前收集关键信息，无需重复追问",
        }

    prompt = CLARIFICATION_PROMPT.format(
        topic=state.get("topic") or "",
        background=state.get("user_background") or "（用户未提供额外背景）",
        decision_model=json.dumps(
            state.get("decision_model") or {},
            ensure_ascii=False,
        ),
    )
    try:
        async with get_llm_semaphore():
            decision = await asyncio.wait_for(
                get_structured_llm(
                    ClarificationDecision, temperature=0.2, fast=True
                ).ainvoke(prompt),
                timeout=10,
            )
        questions = _clean_questions(list(decision.questions))
    except Exception:
        questions = _clean_questions(_fallback_questions(state))
        if questions:
            return {
                "clarification_status": "awaiting_input",
                "clarification_questions": [
                    question.model_dump(mode="json") for question in questions
                ],
                "clarification_reason": "需要补充可能改变结论的用户信息",
            }
        return {
            "clarification_status": "not_needed",
            "clarification_questions": [],
            "clarification_reason": "信息门槛暂不可用，会议继续",
        }

    if not decision.needs_clarification or not questions:
        return {
            "clarification_status": "not_needed",
            "clarification_questions": [],
            "clarification_reason": decision.reason or "没有必须追问的用户信息",
        }
    return {
        "clarification_status": "awaiting_input",
        "clarification_questions": [
            question.model_dump(mode="json") for question in questions
        ],
        "clarification_reason": decision.reason,
    }


def route_after_clarification_gate(state: dict) -> str:
    return (
        "awaiting_input"
        if state.get("clarification_status") == "awaiting_input"
        and state.get("clarification_questions")
        else "research"
    )


def persist_clarification_request(state: dict) -> dict:
    """Persist the pause and release the worker instead of blocking it."""
    parliament_id = state.get("parliament_id")
    questions = list(state.get("clarification_questions") or [])[:3]
    if not parliament_id or not questions:
        return {
            "clarification_status": "not_needed",
            "clarification_questions": [],
        }
    now = datetime.now(timezone.utc).isoformat()
    result = (
        get_supabase()
        .table("parliaments")
        .update(
            {
                "status": "awaiting_input",
                "current_phase": "awaiting_input",
                "clarification_status": "awaiting_input",
                "clarification_questions": questions,
                "clarification_requested_at": now,
                "error_message": None,
            }
        )
        .eq("id", parliament_id)
        .eq("status", "running")
        .execute()
    )
    if not result.data:
        return {
            "clarification_status": "not_needed",
            "clarification_questions": [],
            "clarification_reason": "追问保存失败，会议继续",
        }
    try:
        append_event(
            parliament_id,
            "clarification_requested",
            {
                "questions": questions,
                "reason": state.get("clarification_reason") or "",
            },
        )
    except Exception:
        pass
    return {
        "clarification_status": "awaiting_input",
        "clarification_questions": questions,
        "clarification_waiting": True,
    }


def route_after_clarification_persist(state: dict) -> str:
    return "wait" if state.get("clarification_waiting") else "research"
