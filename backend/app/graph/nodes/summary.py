"""Neutral, evidence-aware decision summary with a deterministic fallback."""

from __future__ import annotations

import json
import re

from app.llm import get_fast_llm


SUMMARY_PROMPT = """你是 Decision Council 的中立主持人，不属于任何专家，也不参加竞选。

## 任务
根据用户问题、专家正式发言和核心争议，形成一份可行动的决策总结。

## 硬约束
- 不得用专家职业身份代替证据
- 明确区分已确认事实、计算结果和推断假设
- 不输出置信度百分比
- 不输出 Agent 工作路径、工具步骤或内部状态
- 必须给出结论翻转条件
- 这是一份辅助决策建议，不替用户作出决定

## 用户问题
{topic}

## 决策模型
{decision_model}

## 专家最终判断
{members_summary}

## 核心争议
{disputes}

## 最近正式讨论
{debate_summary}

## 输出
只返回 JSON：
{{
  "recommendation": "当前建议",
  "recommendation_strength": "conditional|clear|insufficient",
  "key_reason": "最关键理由",
  "consensus": ["共识"],
  "dissent": [
    {{"topic": "争议主题", "sides": {{"专家ID": "观点"}}}}
  ],
  "decision_options": [
    {{"option": "方案", "benefits": ["收益"], "costs": ["代价"]}}
  ],
  "assumptions": ["仍在使用的推断假设"],
  "next_actions": ["下一步行动"],
  "reversal_conditions": ["结论翻转条件"],
  "open_questions": ["仍需用户确认的问题"]
}}"""


def _extract_json_from_response(text: str) -> dict:
    text = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("总结响应中没有 JSON 对象")
    return json.loads(text[start : end + 1])


def _member_summary(state: dict) -> tuple[str, list[dict]]:
    lines: list[str] = []
    stances: list[dict] = []
    for expert_id in state.get("selected_expert_ids", []):
        data = state.get("members", {}).get(expert_id, {})
        stance = data.get("stance", "neutral")
        content = (
            data.get("reasoning_summary") or data.get("content") or "本轮未完成"
        )
        lines.append(f"- [{expert_id}] 立场={stance}: {content[:240]}")
        stances.append(
            {
                "expert_id": expert_id,
                "stance": stance,
                "position": data.get("position"),
                "basis_level": data.get("basis_level", "limited"),
            }
        )
    return "\n".join(lines) or "（无）", stances


def _clean_analysis_basis(state: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {
        "user_facts": [],
        "web_sources": [],
        "calculations": [],
        "inferences": [],
    }
    seen: set[str] = set()

    decision_model = state.get("decision_model", {})
    for index, fact in enumerate(decision_model.get("user_facts", [])):
        key = f"user_{index}_{fact}"
        if key not in seen:
            grouped["user_facts"].append(
                {"evidence_id": key, "content": str(fact)}
            )
            seen.add(key)

    for archive in state.get("agent_results", {}).values():
        for evidence in archive.get("evidence", []):
            evidence_id = str(evidence.get("evidence_id") or "")
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            kind = evidence.get("kind", "inference")
            item = {
                "evidence_id": evidence_id,
                "content": evidence.get("claim_summary", ""),
                "supports_claim": evidence.get("supports_claim", ""),
                "source_title": evidence.get("source_title", ""),
                "source_url": evidence.get("source_url"),
                "source_domain": evidence.get("source_domain", ""),
                "published_at": evidence.get("published_at"),
                "freshness_note": evidence.get("freshness_note"),
                "calculation_inputs": evidence.get("calculation_inputs", {}),
                "calculation_formula": evidence.get("calculation_formula"),
                "assumptions": evidence.get("assumptions", []),
            }
            target = {
                "user_fact": "user_facts",
                "web_source": "web_sources",
                "calculation": "calculations",
                "inference": "inferences",
            }.get(kind, "inferences")
            grouped[target].append(item)

    for expert_id, archive in state.get("agent_results", {}).items():
        for index, assumption in enumerate(
            archive.get("result", {}).get("assumptions", [])
        ):
            key = f"inference_{expert_id}_{index}"
            if key not in seen:
                grouped["inferences"].append(
                    {
                        "evidence_id": key,
                        "content": assumption,
                        "expert_id": expert_id,
                    }
                )
                seen.add(key)
    return grouped


def _basis_assessment(
    analysis_basis: dict[str, list[dict]],
    decision_model: dict,
) -> dict:
    verified_count = sum(
        len(analysis_basis[key])
        for key in ("user_facts", "web_sources", "calculations")
    )
    unknown_count = len(decision_model.get("unknowns", []))
    if verified_count >= 4 and unknown_count <= 1:
        level = "strong"
    elif verified_count >= 1:
        level = "moderate"
    else:
        level = "limited"
    return {
        "level": level,
        "verified_count": verified_count,
        "important_unknown_count": unknown_count,
        "description": {
            "limited": "关键条件仍缺失，当前结论主要依赖条件分析。",
            "moderate": "已有部分事实支持，但仍有重要条件需要确认。",
            "strong": "关键事实和计算较完整，主要反例已经接受检查。",
        }[level],
    }


def _fallback_summary(state: dict) -> dict:
    selected = state.get("selected_expert_ids", [])
    members = state.get("members", {})
    completed = [
        expert_id
        for expert_id in selected
        if members.get(expert_id, {}).get("content")
        or members.get(expert_id, {}).get("reasoning_summary")
    ]
    latest = [
        (
            members.get(expert_id, {}).get("reasoning_summary")
            or members.get(expert_id, {}).get("content")
            or ""
        )
        for expert_id in completed
    ]
    return {
        "recommendation": "现有信息支持先完成关键验证，再作最终决定。",
        "recommendation_strength": "insufficient",
        "key_reason": latest[0][:180] if latest else "关键专家意见尚未完整形成。",
        "consensus": ["不要把尚未核实的推断当成确定事实。"],
        "dissent": [
            {
                "topic": dispute.get("question", "核心争议"),
                "sides": {},
            }
            for dispute in state.get("core_disputes", [])
        ],
        "decision_options": [],
        "assumptions": [],
        "next_actions": list(
            state.get("decision_model", {}).get("research_questions", [])
        )[:3],
        "reversal_conditions": list(
            state.get("decision_model", {}).get("unknowns", [])
        )[:3]
        or ["关键未知条件得到确认后重新评估。"],
        "open_questions": list(
            state.get("decision_model", {}).get("unknowns", [])
        )[:3],
        "fallback_used": True,
    }


def run_summary(state: dict) -> dict:
    """Build a neutral summary; never expose internal Agent execution traces."""
    members_summary, member_positions = _member_summary(state)
    debate_lines = []
    for entry in state.get("transcript", [])[-24:]:
        if entry.get("type") == "debate":
            debate_lines.append(
                f"[{entry.get('speaker')} 第{entry.get('round')}轮]: "
                f"{str(entry.get('content', ''))[:220]}"
            )
    disputes = state.get("core_disputes", [])
    decision_model = state.get("decision_model", {})

    try:
        response = get_fast_llm(temperature=0.35, max_tokens=2600).invoke(
            SUMMARY_PROMPT.format(
                topic=state.get("topic", ""),
                decision_model=json.dumps(decision_model, ensure_ascii=False),
                members_summary=members_summary,
                disputes=json.dumps(disputes, ensure_ascii=False),
                debate_summary="\n".join(debate_lines) or "（无）",
            )
        )
        content = response.content if hasattr(response, "content") else str(response)
        summary = _extract_json_from_response(content)
        summary["fallback_used"] = False
    except Exception:
        summary = _fallback_summary(state)

    defaults = {
        "recommendation": "建议先补充关键信息后再决定。",
        "recommendation_strength": "insufficient",
        "key_reason": "当前判断仍依赖未确认条件。",
        "consensus": [],
        "dissent": [],
        "decision_options": [],
        "assumptions": [],
        "next_actions": [],
        "reversal_conditions": ["关键未知条件得到确认后重新评估。"],
        "open_questions": [],
    }
    for key, value in defaults.items():
        summary.setdefault(key, value)

    analysis_basis = _clean_analysis_basis(state)
    basis_assessment = _basis_assessment(analysis_basis, decision_model)
    summary.update(
        {
            "advice": summary["recommendation"],
            "blind_spots": summary.get("assumptions", []),
            "selected_expert_ids": list(state.get("selected_expert_ids", [])),
            "expert_selection_reasons": state.get("expert_selection_reasons", {}),
            "member_positions": member_positions,
            "member_stances": {
                item["expert_id"]: {
                    "stance": item["stance"],
                    # Legacy field retained only so old clients can parse the shape.
                    "confidence": 0.0,
                }
                for item in member_positions
            },
            "core_disputes": disputes,
            "analysis_basis": analysis_basis,
            "basis_assessment": basis_assessment,
            "round_count": int(state.get("current_round", 0)),
            "chair": state.get("chair"),
            "chair_name": state.get("chair_name") or "中立主持人",
        }
    )
    # Deliberately exclude agent_results, agent_traces, tool calls and timings.
    return {
        "summary": summary,
        "basis_assessment": basis_assessment,
    }
