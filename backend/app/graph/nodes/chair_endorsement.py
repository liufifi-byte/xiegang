"""Quick expert endorsement round — picks a Concept Council chair.

P0 / drama step 3: 在 cross_examination 或 convergence 之后、summary 之前
插一个「快速互荐」节点。每位专家用一句话推荐（除自己以外）同事当议长，
然后计数选最高票。不补三槽位长篇推荐（节省时间 + 避免拖延）。

当选的专家在 summary 中作为"hero label"显示，
但实际总结仍由中立主持人完成（保留新版的客观可信度，
同时给用户一个戏剧性记忆点：「本场议长：数学家」）。

如果出现票数并列或全部推荐失败，中立的主持人兜底。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from app.agents.profiles import EXPERT_PROFILES
from app.llm import get_structured_llm
from app.schemas.member import MemberStatement  # reuse structured output
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ChairEndorsement(BaseModel):
    """One expert's quick endorsement for the chair role."""
    recommender: str = Field(description="推荐人 expert_id")
    recommend_target: str = Field(description="被推荐人 expert_id（不能是自己）")
    reason: str = Field(description="一句话理由，≤60 字")


ENDORSE_PROMPT = """你是 Decision Council 的{expert_name}（{expert_duty_label}），
刚结束了与其他专家的多轮辩论。

##任务
快速互荐一位除你之外的同事担任本场议长（chair）。

##已有信息
用户决策：{topic}

所有专家的本轮立场：
{peers_summary}

##硬约束
- 必须推荐除你自己之外的某一位专家
- 理由必须引用对方刚才的具体一句话或立场（不允许空泛）
- 理由 ≤ 60 字，快节奏给出
- 直接输出 JSON，不要包裹 markdown fence"""


def _peers_summary(state: dict, selected: list[str]) -> str:
    """拿各专家最后一轮发言做摘录。"""
    lines = []
    members = state.get("members", {})
    for expert_id in selected:
        info = members.get(expert_id) or {}
        stance = info.get("stance", "?")
        content = (info.get("content") or info.get("reasoning_summary") or "")[:120]
        display = EXPERT_PROFILES.get(expert_id, EXPERT_PROFILES.get("mathematician")).display_name
        lines.append(f"[{expert_id} / {display}] {stance}: {content}")
    return "\n".join(lines) or "（无）"


async def _one_endorsement(
    expert_id: str,
    state: dict,
    selected: list[str],
) -> ChairEndorsement | None:
    """单个专家的快速推荐。"""
    profile = EXPERT_PROFILES.get(expert_id)
    if not profile:
        return None

    # V3：duty 标签删除——用角色核心职责替代
    duty_label = profile.professional_identity

    prompt = ENDORSE_PROMPT.format(
        expert_name=profile.display_name,
        expert_duty_label=duty_label,
        topic=state.get("topic", "用户决策"),
        peers_summary=_peers_summary(state, selected),
    )

    try:
        structured = get_structured_llm(ChairEndorsement, temperature=0.6, fast=True)
        result = await asyncio.wait_for(structured.ainvoke(prompt), timeout=30)
        # 强制：不能推荐自己
        if result.recommend_target == expert_id:
            # swap 到任何其他专家
            alt = next((e for e in selected if e != expert_id), None)
            if alt:
                result.recommend_target = alt
        # 推荐 target 必须在 selected 范围
        if result.recommend_target not in selected:
            alt = next((e for e in selected if e != expert_id), None)
            if alt:
                result.recommend_target = alt
        return result
    except Exception as e:
        import traceback
        logger.warning("专家 %s 推荐失败: %s\n%s", expert_id, e, traceback.format_exc()[-300:])
        return None


async def run_quick_chair_endorsement(state: dict) -> dict:
    """议长快互荐节点。

    1. 并发让每位专家给一句推荐
    2. 统计选票（self-endorse 不计）
    3. 平票或全失败：fallback=null chair（中立主持人兜底）
    4. 写入 transcript 一条 system 消息：“本场议长：王某某，由 N 位专家推荐”
    5. state 中写 chair / chair_name / election_result
    """
    selected = list(
        state.get("selected_expert_ids")
        or state.get("members", {}).keys()
    )
    if len(selected) < 2:
        # 专家太少不值得选
        return {
            "election_result": {
                "winner": None,
                "votes": {},
                "fallback_used": True,
                "fallback_reason": "experts_insufficient",
            },
            "transcript": state.get("transcript", []) + [
                {
                    "speaker": "system",
                    "type": "election",
                    "content": "本场议长：中立主持人（参与专家不足 2 人）",
                }
            ],
        }

    tasks = [_one_endorsement(eid, state, selected) for eid in selected]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    endorsements = [r for r in results if r is not None]

    # 票数
    votes: dict[str, int] = {}
    recommenders: dict[str, list[str]] = {}
    for e in endorsements:
        votes[e.recommend_target] = votes.get(e.recommend_target, 0) + 1
        recommenders.setdefault(e.recommend_target, []).append(e.recommender)

    # 选出（要≥1 票 + 必须在 selected 内）
    valid_votes = {k: v for k, v in votes.items() if k in selected and v > 0}
    winner = None
    fallback = False
    fallback_reason = None
    if not valid_votes:
        fallback = True
        fallback_reason = "no_valid_vote"
    else:
        max_v = max(valid_votes.values())
        tops = [k for k, v in valid_votes.items() if v == max_v]
        if len(tops) > 1:
            fallback = True
            fallback_reason = "tied"
        else:
            winner = tops[0]

    chair_id = winner or None
    if chair_id:
        profile = EXPERT_PROFILES.get(chair_id)
        chair_name = profile.display_name if profile else (chair_id or "中立主持人")
    else:
        chair_name = "中立主持人"

    # transcript 系统消息（戏剧性记忆点）
    vote_lines = " / ".join(
        f"{EXPERT_PROFILES.get(k, EXPERT_PROFILES.get('mathematician')).display_name} {v}票"
        for k, v in sorted(valid_votes.items(), key=lambda x: -x[1])
    )
    if winner:
        election_msg = f"表决完成。本场议长：{chair_name}（{max_v} 位专家推荐）。票数：{vote_lines}。"
    else:
        election_msg = f"表决未产生明确议长（{fallback_reason}）。本场由中立主持人主持最终总结。"

    election_result = {
        "candidates": [
            {
                "recommender": e.recommender,
                "recommend_target": e.recommend_target,
                "reason": e.reason,
            }
            for e in endorsements
        ],
        "vote_tally": valid_votes,
        "recommenders": recommenders,
        "winner": chair_id,
        "winner_name": chair_name,
        "fallback_used": fallback,
        "fallback_reason": fallback_reason,
        "timestamp": time.time(),
    }

    return {
        "election_result": election_result,
        "chair": chair_id,
        "chair_name": chair_name,
        "transcript": state.get("transcript", []) + [
            {
                "speaker": "system",
                "type": "election",
                "content": election_msg,
            }
        ],
    }
