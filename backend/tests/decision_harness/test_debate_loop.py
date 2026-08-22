"""Phase 5 测试：争议提取、定向辩论、停止条件、用户事实重开。"""
import json

import pytest

from app.decision_harness.debate_loop import (
    DebateRunner,
    evaluate_round,
    parse_debate_response,
    reopen_dispute,
    structural_disputes,
)
from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)
from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
from app.decision_harness.schemas import (
    ConflictType,
    DisputeIssue,
    SubAgentResult,
)


@pytest.fixture(autouse=True)
def _clean():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _result(agent: str, stance: str, judgment: str, status="completed", **over) -> SubAgentResult:
    return SubAgentResult(
        task_id=f"task-{agent}-1", agent_id=agent, status=status,
        stance=stance, judgment=judgment, confidence=0.8, **over,
    )


# ============ 结构化争议提取 ============

def test_structural_disputes_opposing_stances_only():
    results = [
        _result("economist", "oppose", "现金流容错率不足，不建议现在跳"),
        _result("strategist", "support", "窗口期难得，建议接受 Offer"),
        _result("psychologist", "cautious", "动机尚需澄清"),   # 不参与
        _result("historian", "neutral", "先例两说"),           # 不参与
        _result("mathematician", "neutral", "", status="silent"),  # 不参与
    ]
    disputes = structural_disputes(results)
    assert len(disputes) == 1
    d = disputes[0]
    assert sorted(d.participants) == ["economist", "strategist"]
    assert d.importance >= 0.3
    assert len(d.claims) == 2
    assert d.status == "open" and d.round == 0


def test_structural_disputes_conflict_type_heuristics():
    # contrarian 参与 → assumption_conflict
    d1 = structural_disputes([
        _result("contrarian", "oppose", "你们都低估了下行风险"),
        _result("economist", "support", "数字上可行"),
    ])[0]
    assert d1.conflict_type == ConflictType.ASSUMPTION_CONFLICT

    # 双方 unknowns 有事实词 → fact_conflict
    d2 = structural_disputes([
        _result("economist", "oppose", "不行", unknowns=["期权条款未确认"]),
        _result("legal_scholar", "support", "可以", unknowns=["合同数据缺失"]),
    ])[0]
    assert d2.conflict_type == ConflictType.FACT_CONFLICT
    assert d2.missing_variable is not None

    # 风险词 → risk_tolerance_conflict
    d3 = structural_disputes([
        _result("economist", "oppose", "风险太高"),
        _result("strategist", "support", "值得冒险"),
    ])[0]
    assert d3.conflict_type == ConflictType.RISK_TOLERANCE_CONFLICT


def test_no_disputes_when_no_opposition():
    results = [
        _result("economist", "support", "可行"),
        _result("psychologist", "support", "动机一致"),
    ]
    assert structural_disputes(results) == []


# ============ 响应解析与停止条件 ============

def test_parse_debate_response_defaults_and_validation():
    resp = parse_debate_response(
        json.dumps({"position_before": "support", "position_after": "cautious",
                    "changed": True, "reason": "对方数据更有力"}),
        "dispute-1", "economist",
    )
    assert resp.dispute_id == "dispute-1" and resp.agent_id == "economist"
    assert resp.changed is True
    # 立场变了但 changed=false → 契约拒绝 → None
    bad = parse_debate_response(
        json.dumps({"position_before": "support", "position_after": "oppose",
                    "changed": False}),
        "dispute-1", "economist",
    )
    assert bad is None
    assert parse_debate_response("不是 JSON", "d", "a") is None


def test_evaluate_round_stop_conditions():
    def resp(after="support", changed=None, remaining="", user=False, ev=None):
        before = "support"
        return parse_debate_response(json.dumps({
            "position_before": before, "position_after": after,
            "changed": (before != after) if changed is None else changed,
            "remaining_disagreement": remaining,
            "needs_user_input": user, "new_evidence_refs": ev or [],
        }), "d", "x")

    # 立场收敛且无遗留 → resolved
    s, why = evaluate_round([resp(), resp()], round_no=1, max_rounds=2,
                            missing_variable=None)
    assert (s, why) == ("resolved", "positions_converged")
    # 需要用户输入 → narrowed
    s, _ = evaluate_round([resp(user=True)], round_no=1, max_rounds=2,
                          missing_variable=None)
    assert s == "narrowed"
    # 无遗留 + 有待确认变量 → narrowed
    s, why = evaluate_round([resp(after="cautious", remaining=""),
                             resp(after="support", remaining="")],
                            round_no=1, max_rounds=2,
                            missing_variable="期权行权条款")
    assert s == "narrowed" and why == "isolated_to_missing_variable"
    # 连续无新增信息 → resolved（双方立场未变、无新证据）
    s, why = evaluate_round(
        [resp(remaining="仍认为风险高", changed=False),
         resp(remaining="仍有保留", changed=False)],
        round_no=2, max_rounds=2, missing_variable=None)
    assert (s, why) == ("resolved", "no_new_information")
    # 轮次上限 → retained（有新证据但仍保留分歧）
    s, why = evaluate_round(
        [resp(remaining="保留", ev=["ev-new"]),
         resp(remaining="保留", ev=["ev-new2"])],
        round_no=2, max_rounds=2, missing_variable=None)
    assert (s, why) == ("retained", "round_limit")


def test_reopen_dispute_with_user_fact():
    d = DisputeIssue(
        dispute_id="dispute-1", question="是否接受 Offer",
        importance=0.8, conflict_type=ConflictType.FACT_CONFLICT,
        participants=["economist", "strategist"], status="resolved", round=2,
    )
    reopened = reopen_dispute(d, "用户补充：Offer 允许延期两周签署")
    assert reopened.status == "open"
    assert reopened.claims[-1] == {"kind": "user_fact",
                                   "claim": "用户补充：Offer 允许延期两周签署"}
    assert reopened.round == 2  # 总轮次仍受上限约束


# ============ DebateRunner ============

def _final(payload: dict) -> RuntimeEvent:
    return RuntimeEvent(
        seq=0, type="turn_final",
        payload={"final_response": json.dumps(payload, ensure_ascii=False)},
    )


def _dispute() -> DisputeIssue:
    return DisputeIssue(
        dispute_id="dispute-1",
        question="现金流容错是否足够 vs 窗口期价值",
        importance=0.8, conflict_type=ConflictType.RISK_TOLERANCE_CONFLICT,
        claims=[
            {"agent_id": "economist", "stance": "oppose", "claim": "容错不足"},
            {"agent_id": "strategist", "stance": "support", "claim": "窗口难得"},
        ],
        participants=["economist", "strategist"],
    )


def _rows():
    return {
        "economist": {"expert_id": "economist", "task_id": "task-economist-1",
                      "runtime_thread_id": "th-eco"},
        "strategist": {"expert_id": "strategist", "task_id": "task-strategist-2",
                       "runtime_thread_id": "th-str"},
    }


async def _collect(gen):
    return [e async for e in gen]


async def test_debate_resolved_in_one_round():
    eco_sid = "run1:expert:task-economist-1"
    str_sid = "run1:expert:task-strategist-2"
    fake = FakeAgentRuntime(scripts={
        # 主席/专家首轮已消耗 1 轮；辩论恢复后是各自线程的下一轮
        eco_sid: [
            [_final({"status": "completed", "judgment": "首轮"})],  # 首轮（已耗）
            [_final({"position_before": "oppose", "position_after": "cautious",
                     "changed": True, "reason": "窗口论据成立",
                     "remaining_disagreement": ""})],
        ],
        str_sid: [
            [_final({"status": "completed", "judgment": "首轮"})],
            [_final({"position_before": "support", "position_after": "cautious",
                     "changed": True, "reason": "接受折中方案",
                     "remaining_disagreement": ""})],
        ],
    })
    # 模拟首轮已跑：消耗第一轮并 close（保留轮次索引）
    await fake.start_session(eco_sid, instructions="x")
    await fake.start_session(str_sid, instructions="x")
    async for _ in fake.start_turn(eco_sid, "p"): pass
    async for _ in fake.start_turn(str_sid, "p"): pass
    await fake.close_session(eco_sid)
    await fake.close_session(str_sid)

    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    runner = DebateRunner(run_id="run1", dispute=_dispute(),
                          agent_rows=_rows(), runtime=fake, repo=repo)
    events = await _collect(runner.run())

    types = [e["event_type"] for e in events]
    assert types[0] == "dispute_opened"
    assert types.count("debate_message") == 2
    assert "dispute_resolved" in types
    out = runner.outcome
    assert out.dispute.status == "resolved" and out.dispute.round == 1
    assert out.stop_reason == "positions_converged"

    rows = await repo.list_disputes("run1")
    assert rows[0]["status"] == "resolved" and rows[0]["round_count"] == 1
    # 辩论只发生在参与者线程
    assert ("resume_session", eco_sid) in fake.calls
    assert ("resume_session", str_sid) in fake.calls


async def test_debate_round_limit_retained():
    eco_sid = "run1:expert:task-economist-1"
    str_sid = "run1:expert:task-strategist-2"

    def stubborn(before, after):
        return _final({"position_before": before, "position_after": after,
                       "changed": before != after, "reason": "坚持",
                       "remaining_disagreement": "风险判断不同",
                       "new_evidence_refs": ["ev-x"]})

    fake = FakeAgentRuntime(scripts={
        eco_sid: [
            [_final({"status": "completed", "judgment": "x"})],
            [stubborn("oppose", "oppose")], [stubborn("oppose", "oppose")],
        ],
        str_sid: [
            [_final({"status": "completed", "judgment": "x"})],
            [stubborn("support", "support")], [stubborn("support", "support")],
        ],
    })
    await fake.start_session(eco_sid, instructions="x")
    await fake.start_session(str_sid, instructions="x")
    async for _ in fake.start_turn(eco_sid, "p"): pass
    async for _ in fake.start_turn(str_sid, "p"): pass
    await fake.close_session(eco_sid)
    await fake.close_session(str_sid)

    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    runner = DebateRunner(run_id="run1", dispute=_dispute(),
                          agent_rows=_rows(), runtime=fake, repo=repo)
    events = await _collect(runner.run())

    out = runner.outcome
    assert out.dispute.status == "retained" and out.dispute.round == 2
    # 允许保留分歧：仍有 dispute 记录
    rows = await repo.list_disputes("run1")
    assert rows[0]["status"] == "retained"
    assert any(e["payload"].get("note") == "dispute_retained" for e in events)


async def test_debate_participant_invalid_response_isolated():
    eco_sid = "run1:expert:task-economist-1"
    str_sid = "run1:expert:task-strategist-2"
    fake = FakeAgentRuntime(scripts={
        eco_sid: [
            [_final({"status": "completed", "judgment": "x"})],
            [_final({"position_before": "oppose", "position_after": "cautious",
                     "changed": True, "remaining_disagreement": ""})],
        ],
        str_sid: [
            [_final({"status": "completed", "judgment": "x"})],
            [RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "胡言乱语"})],
            [_final({"position_before": "support", "position_after": "cautious",
                     "changed": False, "remaining_disagreement": ""})],
        ],
    })
    await fake.start_session(eco_sid, instructions="x")
    await fake.start_session(str_sid, instructions="x")
    async for _ in fake.start_turn(eco_sid, "p"): pass
    async for _ in fake.start_turn(str_sid, "p"): pass
    await fake.close_session(eco_sid)
    await fake.close_session(str_sid)

    repo = DecisionRunRepository(force_memory=True)
    await repo.create_run("run1", runtime_mode="x", decision_state={}, budget={})
    runner = DebateRunner(run_id="run1", dispute=_dispute(),
                          agent_rows=_rows(), runtime=fake, repo=repo)
    events = await _collect(runner.run())
    # strategist 第 1 轮无效 → 跳过该轮，第 2 轮补上并收敛
    assert any(e["payload"].get("note") == "debate_invalid_response" for e in events)
    assert runner.outcome.dispute.status in ("resolved", "retained")
