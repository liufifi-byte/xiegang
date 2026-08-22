"""Phase 8 测试：灰度选择器、Shadow 运行器隔离、对比与盲评。"""
import json

import pytest

from app.config import get_settings
from app.decision_harness.runtime import AgentRuntimeError
from app.decision_harness.runtime_selector import (
    _hash_percent,
    is_shadow_enabled,
    select_runtime_mode,
)


# ============ 选择器 ============

def test_hash_percent_deterministic_and_bounded():
    assert _hash_percent("p1") == _hash_percent("p1")
    assert 0 <= _hash_percent("anything") <= 99


def test_selector_mode_precedence(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "decision_runtime_mode", "shadow", raising=False)
    assert select_runtime_mode("any") == "shadow"
    assert is_shadow_enabled("any") is True

    monkeypatch.setattr(s, "decision_runtime_mode", "codex_harness", raising=False)
    assert select_runtime_mode("any") == "codex_harness"
    assert is_shadow_enabled("any") is False


def test_selector_canary_buckets(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "decision_runtime_mode", "legacy", raising=False)
    monkeypatch.setattr(s, "decision_runtime_canary_percent", 0, raising=False)
    assert all(select_runtime_mode(f"p{i}") == "legacy" for i in range(50))

    monkeypatch.setattr(s, "decision_runtime_canary_percent", 100, raising=False)
    assert all(select_runtime_mode(f"p{i}") == "codex_harness" for i in range(50))

    monkeypatch.setattr(s, "decision_runtime_canary_percent", 50, raising=False)
    modes = [select_runtime_mode(f"p{i}") for i in range(200)]
    assert "legacy" in modes and "codex_harness" in modes
    # 同一议会永远同桶
    assert select_runtime_mode("fixed-id") == select_runtime_mode("fixed-id")


def test_selector_rejects_invalid_canary(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "decision_runtime_mode", "legacy", raising=False)
    monkeypatch.setattr(s, "decision_runtime_canary_percent", 150, raising=False)
    with pytest.raises(AgentRuntimeError, match="invalid_canary"):
        select_runtime_mode("p")


# ============ Shadow Runner ============

async def test_shadow_full_chain_with_fake_runtime():
    """注入 Fake：全链（计划→专家→辩论→终局）跑通且只写 decision_runs。"""
    from app.decision_harness.repository import (
        DecisionRunRepository,
        _clear_memory_for_tests,
    )
    from app.decision_harness.runtime import FakeAgentRuntime, RuntimeEvent
    from app.decision_harness.shadow_runner import run_shadow

    _clear_memory_for_tests()
    main_sid = "sh:main"
    eco_sid = "sh:expert:task-economist-1"
    psy_sid = "sh:expert:task-psychologist-2"
    con_sid = "sh:expert:task-contrarian-3"

    def final(payload):
        return RuntimeEvent(
            seq=0, type="turn_final",
            payload={"final_response": json.dumps(payload, ensure_ascii=False)})

    plan = {
        "plan_version": 1, "objective": "两周内完成跳槽决策评估",
        "expert_tasks": ["economist: 现金流压力测试", "psychologist: 动机评估",
                         "contrarian: 反方论证"],
        "created_from_state_version": 1,
    }
    ack = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "已收到"})
    nonsense = RuntimeEvent(seq=0, type="turn_final", payload={"final_response": "嗯"})

    def expert_result(agent, stance, judgment):
        return final({"status": "completed", "stance": stance,
                      "judgment": judgment, "confidence": 0.8,
                      "action": "先拿书面条款", "reversal_conditions": ["存款补充"]})

    def debate(pid, before, after):
        return final({"dispute_id": "dispute-1", "agent_id": pid,
                      "position_before": before, "position_after": after,
                      "changed": before != after,
                      "reason": "论据成立" if before != after else "",
                      "remaining_disagreement": ""})

    fake = FakeAgentRuntime(scripts={
        main_sid: [
            [final({"action": "revise_plan", "payload": {"plan": plan}})],
            [ack],  # 专家观察确认
            [ack],  # 辩论观察
        ],
        eco_sid: [[expert_result("economist", "oppose", "现金流容错率不足，留下更稳")],
                  [debate("economist", "oppose", "cautious")]],
        psy_sid: [[expert_result("psychologist", "cautious", "动机偏逃避，建议缓行")],
                  [debate("psychologist", "cautious", "cautious")]],
        con_sid: [[nonsense], [nonsense], [nonsense],  # 2 轮预算+强制收敛 → failed
                  ],
    })
    # psy 的辩论轮（Round1 参与）；contrarian failed 不进辩论
    fake._scripts[con_sid] = [[nonsense]] * 3

    repo = DecisionRunRepository(force_memory=True)
    task = {"topic": "要不要从大厂跳去创业公司", "user_background": "存款 150 万"}

    run_id = await run_shadow(
        "parl-1", task, runtime=fake, run_id="sh",
    )
    assert run_id == "sh"
    row = await repo.get_run("sh")
    assert row["status"] == "shadow_completed"
    state = row["decision_state"]
    assert state["final_report"]["expert_findings"]
    # 影子事件不落 parliament_events：本测试未接触 event_store，
    # 且 decision_state 里只有结构化结果
    assert "transcript" not in state


async def test_shadow_skips_without_runtime(monkeypatch):
    from app.decision_harness.repository import (
        DecisionRunRepository,
        _clear_memory_for_tests,
    )
    from app.decision_harness import shadow_runner as sr

    _clear_memory_for_tests()
    monkeypatch.setattr(sr, "_sdk_available", lambda: False)
    run_id = await sr.run_shadow("parl-2", {"topic": "换城市工作吗"}, run_id="sh2")
    repo = DecisionRunRepository(force_memory=True)
    row = await repo.get_run("sh2")
    assert row["status"] == "shadow_skipped"
    assert row["error"] == "codex_sdk_unavailable"
    assert run_id == "sh2"


async def test_launch_shadow_disabled_is_noop(monkeypatch):
    from app.decision_harness import shadow_runner as sr

    s = get_settings()
    monkeypatch.setattr(s, "decision_runtime_mode", "legacy", raising=False)
    assert sr.launch_shadow_if_enabled("p", {"topic": "x"}) is None


# ============ 对比与盲评 ============

def test_build_comparison_and_aggregate():
    from evals.shadow_compare import aggregate, build_comparison

    legacy = {"status": "done", "total_duration_seconds": 169, "summary": {"x": 1}}
    shadow_row = {
        "status": "shadow_completed", "exit_status": "FINALIZED",
        "decision_state": {"final_report": {
            "recommendation": {"option": "留下", "strength": "conditional"},
            "remaining_uncertainties": ["期权条款"],
            "reversal_conditions": ["存款补充"],
            "expert_findings": [
                {"stance": "oppose"}, {"stance": "cautious"}, {"stance": "oppose"},
            ],
        }},
    }
    c = build_comparison(parliament_id="p1", legacy=legacy,
                         shadow_row=shadow_row, shadow_evidence=[{}, {}])
    assert c["both_completed"] is True
    assert c["shadow"]["recommendation_strength"] == "conditional"
    assert c["shadow"]["stance_diversity"] == 2
    assert c["shadow"]["evidence_count"] == 2

    agg = aggregate([c, build_comparison(
        parliament_id="p2", legacy={"status": "failed"},
        shadow_row={"status": "shadow_completed", "decision_state": {}},
    )])
    assert agg["runs"] == 2
    assert agg["both_completed"] == 1
    assert agg["legacy_completed_rate"] == 0.5
    assert agg["strength_distribution"] == {"conditional": 1}


def test_blind_pair_anonymous_and_reproducible():
    from evals.shadow_compare import render_blind_pair

    md = render_blind_pair(
        legacy_summary={"recommendation": {"option": "留下"}, "summary_text": "要点A"},
        harness_report={"recommendation": {"option": "跳槽", "strength": "clear"},
                        "summary": "要点B",
                        "remaining_uncertainties": [], "reversal_conditions": []},
        seed="parl-1",
    )
    assert "方案 X" in md and "方案 Y" in md
    assert "要点A" in md and "要点B" in md
    assert "codex" not in md.lower() and "legacy" not in md.lower()  # 匿名
    # 同种子可复现（复核用），不同种子可能互换
    assert md == render_blind_pair(
        legacy_summary={"recommendation": {"option": "留下"}, "summary_text": "要点A"},
        harness_report={"recommendation": {"option": "跳槽", "strength": "clear"},
                        "summary": "要点B",
                        "remaining_uncertainties": [], "reversal_conditions": []},
        seed="parl-1",
    )
    # 缺报告侧也有可评文本
    empty = render_blind_pair(legacy_summary=None, harness_report=None, seed="s")
    assert "未产出" in empty
