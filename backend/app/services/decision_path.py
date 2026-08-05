"""决策路径服务 - PRD §14.1-§14.5 实现。

职责:
  - 根据 Playbook + 用户输入生成任务简报
  - 提取用户事实
  - 识别和排序关键问题(最多 3 个)
  - 生成问题选项和保守假设
  - 处理用户修改意见
  - 生成草稿 id + 草稿 DB 持久化

不职责:
  - 不创建会议(留给 parliament.py)
  - 不调用专家 Agent
  - 不生成最终决策结论

遵循 LLM 强校验 + Playbook 兜底策略。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.config import get_settings
from app.db import direct_db_url, get_supabase
from app.llm import get_structured_llm
from app.llm_concurrency import get_llm_semaphore
from app.playbooks import Playbook, QuestionTemplate, select_playbook, UNKNOWN_OPTION
from app.schemas.decision_path import (
    AnswerItem,
    Assumption,
    DecisionBrief,
    DecisionPathDraft,
    DecisionPathPreviewRequest,
    DecisionPathPreviewResponse,
    DecisionQuestion,
    DecisionQuestionOption,
    ExtractedFact,
    LLMDecisionOutput,
    LLMExtractedFactLLM,
)
from pydantic import ValidationError
import psycopg

logger = logging.getLogger(__name__)

LLM_TIMEOUT_SECONDS = 12.0  # PRD §16.1 总超时 8-12s


EXTRACTION_PROMPT_TEMPLATE = """你是「决策路径」信息提取 Agent。

用户原始问题:
{topic}

用户背景:
{background}

类别: {category}

任务:
1. 重新生成一个清晰、可执行的决策目标(不可与用户原意冲突)
2. 列出 2-4 个比较方案的具体描述
3. 列出 3-5 个分析维度
4. 列出最终给用户的具体交付
5. 从用户原文里识别可量化、可定位、可枚举的明确事实(城市/金额/期限/百分比/截止时间等)
6. 从下面候选问题 id 里挑出最相关的、最多 3 个,只挑「原问题与背景里没给的」
   - 严格按用户专属信息原则(只有用户知道、很可能翻转结论),不挑公开事实问题

候选问题 id:
{candidate_ids}

请用 JSON 输出。

严格规则:
- 决策目标必须基于用户的真实意图,不要套模板
- 不要把候选问题里的全部都选上,只选最关键的 1-3 个
- 若用户没提供关键事实,fact 字段填空数组
"""


async def _safe_llm_call(
    playbook: Playbook,
    topic: str,
    background: str,
    category: str,
    prev_brief: dict | None,
    feedback: str,
) -> dict | None:
    """LLM 调用 + 强校验,失败返回 None。"""
    candidate_ids = [q.id for q in playbook.candidate_questions]
    prompt = EXTRACTION_PROMPT_TEMPLATE.format(
        topic=topic,
        background=background or "(未提供)",
        category=category,
        candidate_ids=", ".join(candidate_ids),
    )
    if prev_brief or feedback:
        prompt += f"\n\n前一次简报(参考): {json.dumps(prev_brief or {}, ensure_ascii=False)}\n"
        prompt += f"用户修改意见(必须优先): {feedback}\n"

    try:
        llm = get_structured_llm(LLMDecisionOutput, temperature=0.3, max_tokens=1500)
        sem = get_llm_semaphore()
        async with sem:
            start = time.monotonic()
            result: LLMDecisionOutput = await llm.ainvoke(prompt)  # type: ignore[assignment]
            elapsed = time.monotonic() - start
            logger.info("decision_path LLM done in %.2fs", elapsed)
            return result.model_dump()
    except (ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("decision_path LLM 失败,走 playbooks 兜底: %s", exc)
        return None


def _build_fallback(
    playbook: Playbook, topic: str, background: str
) -> dict:
    """LLM 失败时用 Playbook 兜底输出(PRD §14.2 / §16.1)。"""
    # 选前 3 个候选问题
    questions = playbook.candidate_questions[:3]
    brief = playbook.to_brief_dict()
    # 让 goal 提及用户的话题
    brief["decision_goal"] = brief["decision_goal"] + f"（针对: {topic[:60]}）"
    return {
        "decision_goal": brief["decision_goal"],
        "decision_options": brief["decision_options"],
        "analysis_dimensions": brief["analysis_dimensions"],
        "final_deliverables": brief["final_deliverables"],
        "extracted_facts": [],
        "selected_question_ids": [q.id for q in questions],
        "fallback_used": True,
    }


def _build_questions(
    playbook: Playbook, selected_ids: list[str]
) -> list[DecisionQuestion]:
    """从选中的 id 拼出完整 Question(PRD §13.3)。"""
    by_id = {q.id: q for q in playbook.candidate_questions}
    result: list[DecisionQuestion] = []
    for qid in selected_ids[:3]:  # 硬上限 3
        tpl: QuestionTemplate | None = by_id.get(qid)
        if tpl is None:
            continue
        result.append(
            DecisionQuestion(
                id=tpl.id,
                question=tpl.question,
                why_it_matters=tpl.why_it_matters,
                answer_type=tpl.answer_type,  # type: ignore[arg-type]
                options=[
                    DecisionQuestionOption(value=v, label=lbl)
                    for v, lbl in tpl.options
                ],
                allow_custom=tpl.allow_custom,
                unknown_assumption=tpl.unknown_assumption,
            )
        )
    # 兜底: LLM 选了空 list 或全部无效时, 用 playbook 前 3 个候选
    if not result:
        for tpl in playbook.candidate_questions[:3]:
            result.append(
                DecisionQuestion(
                    id=tpl.id,
                    question=tpl.question,
                    why_it_matters=tpl.why_it_matters,
                    answer_type=tpl.answer_type,  # type: ignore[arg-type]
                    options=[
                        DecisionQuestionOption(value=v, label=lbl)
                        for v, lbl in tpl.options
                    ],
                    allow_custom=tpl.allow_custom,
                    unknown_assumption=tpl.unknown_assumption,
                )
            )
    return result


def _compute_assumptions(
    playbook: Playbook, answers: list[AnswerItem]
) -> list[Assumption]:
    """从「不确定」答案生成显式假设(PRD §4.4 / §6.6)。"""
    unknown_value = UNKNOWN_OPTION[0]  # "unknown"
    by_id = {q.id: q for q in playbook.candidate_questions}
    result: list[Assumption] = []
    for ans in answers:
        # single/range 用 str; multi 用 list,如果包含 unknown 也算
        is_unknown = ans.value == unknown_value or (
            isinstance(ans.value, list) and unknown_value in ans.value
        )
        if not is_unknown:
            continue
        tpl = by_id.get(ans.question_id)
        if not tpl or not tpl.unknown_assumption:
            continue
        result.append(
            Assumption(
                question_id=ans.question_id,
                assumption_text=tpl.unknown_assumption,
                conclusion_flip_condition="用户提供更多信息后会予以替换并重新评估。",
            )
        )
    return result


# =================== 草稿持久化 (Supabase Postgres) ===================


def _persist_draft(
    generation_id: str,
    device_id: str,
    topic: str,
    topic_category: str | None,
    user_background: str | None,
    selected_expert_ids: list[str] | None,
    payload: dict,
    revision: int,
) -> None:
    supabase = get_supabase()
    row = {
        "id": generation_id,
        "device_id": device_id,
        "version": payload.get("version", "1.0"),
        "topic": topic,
        "topic_category": topic_category,
        "user_background": user_background,
        "selected_expert_ids": selected_expert_ids or [],
        "payload": json.dumps(payload, ensure_ascii=False),
        "revision": revision,
        "status": "active",
    }
    supabase.table("decision_path_drafts").upsert(row, on_conflict="id").execute()


def _load_draft(generation_id: str, device_id: str) -> dict | None:
    supabase = get_supabase()
    res = (
        supabase.table("decision_path_drafts")
        .select("payload,revision,status,expires_at,device_id")
        .eq("id", generation_id)
        .single()
        .execute()
    )
    if not res.data:
        return None
    if res.data["device_id"] != device_id:
        # 其它设备的草稿视为不存在,避免泄露(PRD §13.2)
        return None
    if res.data["status"] != "active":
        return None
    payload = res.data["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    payload["_revision"] = res.data["revision"]
    return payload


def _deprecated_load_via_psycopg(*_a, **_kw):  # noqa: D401
    return None


# =================== Public API ===================


async def generate_preview(
    req: DecisionPathPreviewRequest, device_id: str
) -> DecisionPathPreviewResponse:
    """生成或修订决策路径预览。"""
    playbook = select_playbook(req.topic_category)
    background = req.user_background or ""

    # 是否在修订
    prev_brief = None
    revision = 1
    existing_answers_for_payload: list[AnswerItem] = []
    if req.generation_id:
        prev = _load_draft(req.generation_id, device_id)
        if prev:
            prev_brief = prev.get("brief")
            revision = int(prev.get("_revision", 1)) + 1
            existing_answers_for_payload = [
                AnswerItem(**a) for a in req.existing_answers
            ]

    # LLM 提取事实 + 选择问题
    llm_out = await _safe_llm_call(
        playbook=playbook,
        topic=req.topic,
        background=background,
        category=playbook.category,
        prev_brief=prev_brief,
        feedback=req.brief_feedback or "",
    )
    fallback_used = False
    if llm_out is None:
        llm_out = _build_fallback(playbook, req.topic, background)
        fallback_used = True

    brief = DecisionBrief(
        decision_goal=llm_out["decision_goal"],
        decision_options=llm_out["decision_options"],
        analysis_dimensions=llm_out["analysis_dimensions"],
        autonomous_research=list(playbook.autonomous_research),
        final_deliverables=llm_out["final_deliverables"],
    )
    selected_question_ids = (
        llm_out.get("selected_question_ids")
        or [q.id for q in playbook.candidate_questions[:3]]
    )
    questions = _build_questions(playbook, selected_question_ids)

    extracted_facts = [
        ExtractedFact(
            id=f["id"],
            label=f["label"],
            value=str(f["value"])[:240],
        )
        for f in llm_out.get("extracted_facts", [])
    ]

    # 计算保守假设(如果有 existing_answers, 一般用户重新生成时)
    assumptions = _compute_assumptions(playbook, existing_answers_for_payload)

    # 新 generation_id 或沿用原 id
    generation_id = req.generation_id or f"dp_{uuid.uuid4().hex[:16]}"

    payload = {
        "version": "1.0",
        "category": playbook.category,
        "brief": brief.model_dump(),
        "extracted_facts": [f.model_dump() for f in extracted_facts],
        "questions": [q.model_dump() for q in questions],
        "assumptions": [a.model_dump() for a in assumptions],
    }

    _persist_draft(
        generation_id=generation_id,
        device_id=device_id,
        topic=req.topic,
        topic_category=req.topic_category,
        user_background=req.user_background,
        selected_expert_ids=req.selected_expert_ids,
        payload=payload,
        revision=revision,
    )

    return DecisionPathPreviewResponse(
        version="1.0",
        generation_id=generation_id,
        category=playbook.category,
        brief=brief,
        extracted_facts=extracted_facts,
        questions=questions,
        assumptions=assumptions,
        fallback_used=fallback_used,
    )


def load_preview_by_id(generation_id: str, device_id: str) -> DecisionPathDraft | None:
    raw = _load_draft(generation_id, device_id)
    if raw is None:
        return None
    raw.pop("_revision", None)
    return DecisionPathDraft(
        version=raw.get("version", "1.0"),
        category=raw.get("category", "general"),
        brief=DecisionBrief(
            **{
                k: raw["brief"].get(k)
                for k in (
                    "decision_goal",
                    "decision_options",
                    "analysis_dimensions",
                    "autonomous_research",
                    "final_deliverables",
                )
            }
        ),
        extracted_facts=[ExtractedFact(**f) for f in raw.get("extracted_facts", [])],
        questions=[DecisionQuestion(**q) for q in raw.get("questions", [])],
        assumptions=[Assumption(**a) for a in raw.get("assumptions", [])],
    )


def consume_draft(generation_id: str, device_id: str) -> bool:
    """会议创建成功时把草稿标记为 consumed。返回是否还有效可用。"""
    supabase = get_supabase()
    raw = _load_draft(generation_id, device_id)
    if raw is None:
        return False
    supabase.table("decision_path_drafts").update(
        {"status": "consumed", "consumed_at": "now()".replace("now()", "")}
    ).eq("id", generation_id).execute()
    return True


def build_expert_payload_from_draft(
    draft: DecisionPathDraft, answers: list[AnswerItem]
) -> dict:
    """会议创建成功后, 把 draft + answers 组合成专家上下文里的 decision_path。

    专家上下文里包含(PRD §14.5):
      - 用户原始问题(外层)
      - 用户确认的决策目标
      - 比较方案
      - 用户明确事实
      - 用户关键答案(附与 question_id 对应的 label)
      - 系统假设
      - AI 自主调研清单
      - 最终交付要求
    """
    q_by_id = {q.id: q for q in draft.questions}
    structured_answers = []
    for ans in answers:
        q = q_by_id.get(ans.question_id)
        if not q:
            continue
        # value 是 list[str] 还是 str, 都把 label 找出来
        if isinstance(ans.value, list):
            labels = [
                next((o.label for o in q.options if o.value == v), v)
                for v in ans.value
            ]
            value_str = " / ".join(labels)
        else:
            value_str = next(
                (o.label for o in q.options if o.value == ans.value),
                str(ans.value),
            )
        structured_answers.append(
            {"question_id": ans.question_id, "question": q.question, "answer": value_str}
        )
    return {
        "version": draft.version,
        "category": draft.category,
        "brief": draft.brief.model_dump(),
        "extracted_facts": [f.model_dump() for f in draft.extracted_facts],
        "answers": structured_answers,
        "assumptions": [a.model_dump() for a in draft.assumptions],
    }
