"""Debate Loop（spec 2026-08-22 §9，Phase 5）。

流程（§9.3）：
    提取分歧（结构化提取器；主席 LLM 提取可后接）
    → 生成定向 Debate Packet（只发给相关参与者，§9.1 不全员群聊）
    → 被质询专家经原线程回应（DebateTurnResponse，立场前后结构）
    → §9.4 停止条件判定 → resolved / narrowed / retained
    → 用户事实注入可局部重开（reopen_dispute）

隔离：辩论回合走参与者自己的专家线程（resume），不新建全局会话；
任何解析失败/线程缺失只跳过该参与者，不影响其他争议。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.decision_harness.event_bridge import make_product_event
from app.decision_harness.expert_loop import _extract_json_object
from app.decision_harness.repository import DecisionRunRepository
from app.decision_harness.runtime import AgentRuntime, AgentRuntimeError
from app.decision_harness.schemas import (
    ConflictType,
    DebateTurnResponse,
    DisputeIssue,
    SubAgentResult,
)

_OPPOSING = {frozenset({"support", "oppose"})}
_RISK_MARKERS = ("风险", "容错", "止损", "安全边际", "risk")
_FACT_MARKERS = ("数据", "条款", "事实", "统计", "比率", "价格")


def structural_disputes(
    results: list[SubAgentResult],
    *,
    min_importance: float = 0.3,
) -> list[DisputeIssue]:
    """确定性争议提取：立场对立配对 → DisputeIssue。

    规则（§9.2）：
    - 只取 completed/partial 且有判断的专家；
    - support vs oppose 视为实质分歧；cautious/neutral 不参与；
    - conflict_type 启发式：contrarian 参与 → assumption_conflict；
      双方 unknowns 命中事实词 → fact_conflict；风险词 →
      risk_tolerance_conflict；否则 value_conflict；
    - importance = 0.5 + 双方置信度均值的一半（截到 [0.3, 1.0]）。
    """
    speaking = [
        r for r in results
        if r.status in ("completed", "partial") and r.judgment.strip()
    ]
    disputes: list[DisputeIssue] = []
    seen_pairs: set[frozenset[str]] = set()
    seq = 0
    for i, a in enumerate(speaking):
        for b in speaking[i + 1:]:
            pair = frozenset({a.agent_id, b.agent_id})
            if len(pair) < 2 or pair in seen_pairs:
                continue
            if frozenset({a.stance, b.stance}) not in _OPPOSING:
                continue
            seen_pairs.add(pair)
            seq += 1
            texts = " ".join(
                a.unknowns + b.unknowns + [a.judgment, b.judgment]
            )
            if "contrarian" in pair:
                conflict = ConflictType.ASSUMPTION_CONFLICT
            elif any(m in texts for m in _FACT_MARKERS):
                conflict = ConflictType.FACT_CONFLICT
            elif any(m in texts for m in _RISK_MARKERS):
                conflict = ConflictType.RISK_TOLERANCE_CONFLICT
            else:
                conflict = ConflictType.VALUE_CONFLICT
            importance = min(
                1.0, max(min_importance, 0.5 + (a.confidence + b.confidence) / 4)
            )
            missing = next((u for u in a.unknowns + b.unknowns
                            if any(m in u for m in _FACT_MARKERS)), None)
            disputes.append(DisputeIssue(
                dispute_id=f"dispute-{seq}",
                question=f"{a.judgment[:60]}——与——{b.judgment[:60]}",
                importance=round(importance, 2),
                conflict_type=conflict,
                claims=[
                    {"agent_id": a.agent_id, "stance": a.stance,
                     "claim": a.judgment[:200]},
                    {"agent_id": b.agent_id, "stance": b.stance,
                     "claim": b.judgment[:200]},
                ],
                participants=[a.agent_id, b.agent_id],
                missing_variable=missing,
                status="open",
                round=0,
            ))
    return disputes


def parse_debate_response(
    final_response: str | None, dispute_id: str, agent_id: str
) -> DebateTurnResponse | None:
    payload = _extract_json_object(final_response or "")
    if payload is None:
        return None
    payload.setdefault("dispute_id", dispute_id)
    payload.setdefault("agent_id", agent_id)
    payload.setdefault("position_before", "neutral")
    payload.setdefault("position_after", payload["position_before"])
    payload.setdefault("changed", False)
    try:
        return DebateTurnResponse.model_validate(payload)
    except Exception:
        return None


def evaluate_round(
    responses: list[DebateTurnResponse],
    *,
    round_no: int,
    max_rounds: int,
    missing_variable: str | None,
) -> tuple[str, str]:
    """§9.4 停止条件 → (新状态, 原因)。

    返回状态：resolved / narrowed / retained / open。
    """
    if not responses:
        return ("retained", "no_valid_responses") if round_no >= 1 else ("open", "")
    if any(r.needs_user_input for r in responses):
        return "narrowed", "needs_user_input"
    positions = {r.position_after for r in responses}
    no_remaining = all(not r.remaining_disagreement.strip() for r in responses)
    if no_remaining and len(positions) == 1:
        return "resolved", "positions_converged"
    if no_remaining and missing_variable:
        return "narrowed", "isolated_to_missing_variable"
    new_info = any(r.changed or r.new_evidence_refs for r in responses)
    if not new_info and round_no >= 1:
        return "resolved", "no_new_information"
    if round_no >= max_rounds:
        return "retained", "round_limit"
    return "open", ""


def reopen_dispute(dispute: DisputeIssue, user_fact: str) -> DisputeIssue:
    """用户事实注入后局部重开（spec Phase 5 任务 5）。"""
    claims = list(dispute.claims) + [{"kind": "user_fact", "claim": user_fact[:200]}]
    return dispute.model_copy(update={
        "status": "open",
        "claims": claims,
        "round": dispute.round,  # 轮次上限仍受 max_rounds 约束
    })


@dataclass
class DebateOutcome:
    dispute: DisputeIssue
    transcript: list[DebateTurnResponse] = field(default_factory=list)
    stop_reason: str = ""


class DebateRunner:
    """跑单个争议的定向辩论（每参与者经原线程，最多 max_rounds 轮）。"""

    def __init__(
        self,
        *,
        run_id: str,
        dispute: DisputeIssue,
        agent_rows: dict[str, dict[str, Any]],  # expert_id -> agent_runs 行
        runtime: AgentRuntime,
        repo: DecisionRunRepository,
        max_rounds: int = 2,
        timeout_seconds: float = 180.0,
        ledger_snapshot: str = "",
    ):
        self.run_id = run_id
        self.dispute = dispute
        self.agent_rows = agent_rows
        self.runtime = runtime
        self.repo = repo
        self.max_rounds = max(1, max_rounds)
        self.timeout_seconds = timeout_seconds
        self.ledger_snapshot = ledger_snapshot
        self.outcome: DebateOutcome | None = None

    def _event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        return make_product_event(self.run_id, event_type, payload)

    def _packet_prompt(self, positions: dict[str, str], target: str) -> str:
        others = [
            {"agent_id": pid, "position": positions.get(pid, "neutral"),
             "claim": next((c.get("claim", "") for c in self.dispute.claims
                            if c.get("agent_id") == pid), "")}
            for pid in self.dispute.participants if pid != target
        ]
        packet = {
            "dispute_id": self.dispute.dispute_id,
            "question": self.dispute.question,
            "conflict_type": self.dispute.conflict_type.value
            if hasattr(self.dispute.conflict_type, "value")
            else str(self.dispute.conflict_type),
            "your_position": positions.get(target, "neutral"),
            "other_side": others,
            "shared_evidence_refs": self.dispute.shared_evidence_refs,
            "missing_variable": self.dispute.missing_variable,
            "round": self.dispute.round + 1,
        }
        parts = [
            "【定向质询】针对以下实质分歧，只回应与你专业相关的部分。",
            json.dumps(packet, ensure_ascii=False, indent=2),
        ]
        if self.ledger_snapshot:
            parts.append(self.ledger_snapshot)
        parts.append(
            "只输出一个 JSON 对象（DebateTurnResponse）："
            '{"dispute_id":"…","agent_id":"…","position_before":"support|oppose|'
            'cautious|neutral|defer|conditional_support","position_after":"…",'
            '"changed":true|false,"reason":"…","new_evidence_refs":[],'
            '"remaining_disagreement":"…","needs_user_input":false}'
            "\n立场变化必须 changed=true 并给 reason；允许保留分歧，不强制一致。\n"
            "【回应硬规则】回应必须包含以下三者之一，否则判定为"
            "『维持原立场、无新增信息』并结束本争议：①新数字（先与账本对账）；"
            "②新条件（此前未提过的成立/反转条件）；③新证据引用。"
            "禁止『某某的论证不能改变我的判断』这类不交锋的模板句。"
        )
        return "\n".join(parts        )

    async def run(self) -> AsyncIterator[dict[str, Any]]:
        dispute = self.dispute
        yield self._event("dispute_opened", {
            "dispute_id": dispute.dispute_id,
            "question": dispute.question[:120],
            "importance": dispute.importance,
            "participants": dispute.participants,
        })

        # 参与者当前立场：从首轮 claims 推导
        positions = {
            c["agent_id"]: c.get("stance", "neutral")
            for c in dispute.claims if c.get("agent_id")
        }
        transcript: list[DebateTurnResponse] = []
        stop_reason = ""

        try:
            async with asyncio.timeout(self.timeout_seconds):
                while dispute.status == "open":
                    round_no = dispute.round + 1
                    responses: list[DebateTurnResponse] = []
                    for pid in dispute.participants:
                        row = self.agent_rows.get(pid)
                        if not row or not row.get("runtime_thread_id"):
                            yield self._event("run_log", {
                                "note": "debate_participant_unavailable",
                                "dispute_id": dispute.dispute_id, "agent_id": pid,
                            })
                            continue
                        session_id = f"{self.run_id}:expert:{row['task_id']}"
                        try:
                            await self.runtime.resume_session(
                                session_id,
                                runtime_thread_id=row["runtime_thread_id"],
                            )
                        except AgentRuntimeError:
                            continue  # 线程缺失：跳过该参与者（隔离）
                        final: str | None = None
                        async for event in self.runtime.start_turn(
                            session_id,
                            self._packet_prompt(positions, pid),
                        ):
                            if event.type == "turn_final":
                                final = event.payload.get("final_response")
                        resp = parse_debate_response(
                            final, dispute.dispute_id, pid
                        )
                        if resp is None:
                            yield self._event("run_log", {
                                "note": "debate_invalid_response",
                                "dispute_id": dispute.dispute_id, "agent_id": pid,
                            })
                            continue
                        responses.append(resp)
                        transcript.append(resp)
                        positions[pid] = resp.position_after
                        yield self._event("debate_message", {
                            "dispute_id": dispute.dispute_id,
                            "agent_id": pid,
                            "position_before": resp.position_before,
                            "position_after": resp.position_after,
                            "changed": resp.changed,
                        })

                    dispute = dispute.model_copy(update={"round": round_no})
                    status, stop_reason = evaluate_round(
                        responses, round_no=round_no,
                        max_rounds=self.max_rounds,
                        missing_variable=dispute.missing_variable,
                    )
                    dispute = dispute.model_copy(update={"status": status})

            if dispute.status == "resolved":
                yield self._event("dispute_resolved", {
                    "dispute_id": dispute.dispute_id,
                    "reason": stop_reason,
                    "rounds": dispute.round,
                })
            elif dispute.status == "narrowed":
                yield self._event("run_log", {
                    "note": "dispute_narrowed",
                    "dispute_id": dispute.dispute_id,
                    "reason": stop_reason,
                    "missing_variable": dispute.missing_variable,
                })
            else:  # retained
                yield self._event("run_log", {
                    "note": "dispute_retained",
                    "dispute_id": dispute.dispute_id,
                    "reason": stop_reason or "open_at_exit",
                })
        except TimeoutError:
            dispute = dispute.model_copy(update={"status": "retained"})
            yield self._event("run_log", {
                "note": "dispute_retained",
                "dispute_id": dispute.dispute_id, "reason": "timeout",
            })
        except Exception as exc:  # 辩论崩溃不影响 Run
            dispute = dispute.model_copy(update={"status": "retained"})
            yield self._event("run_log", {
                "note": "dispute_retained",
                "dispute_id": dispute.dispute_id,
                "reason": f"error:{type(exc).__name__}",
            })

        await self.repo.upsert_dispute(
            self.run_id,
            dispute_key=dispute.dispute_id,
            question=dispute.question,
            importance=dispute.importance,
            conflict_type=getattr(dispute.conflict_type, "value", str(dispute.conflict_type)),
            participants=dispute.participants,
            claims=dispute.claims,
            status=dispute.status,
            round_count=dispute.round,
            resolution={"stop_reason": stop_reason},
        )
        self.outcome = DebateOutcome(
            dispute=dispute, transcript=transcript, stop_reason=stop_reason,
        )
