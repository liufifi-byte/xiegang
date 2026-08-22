"""Context Compiler 测试：任务包工厂、上下文隔离、Root 观察白名单。"""
import pytest

from app.decision_harness.context_compiler import (
    build_expert_task_packet,
    compile_child_context,
    render_child_finalize_prompt,
    render_child_prompt,
    root_observation,
)
from app.decision_harness.schemas import DecisionModel, PlanSpec, SubAgentResult

DECISION = DecisionModel(
    question="要不要从大厂跳去创业公司",
    options=["跳槽", "留下"],
    constraints=["现金流最多撑 12 个月"],
    verified_user_facts=["当前年薪 60 万", "有 150 万存款"],
)
PLAN = PlanSpec(
    plan_version=1,
    objective="两周内完成跳槽决策的信息收集与风险评估",
    expert_tasks=["economist: 现金流压力测试"],
    blocking_unknowns=["期权行权条件"],
    stop_conditions=["blocking unknown 解除"],
    created_from_state_version=1,
)


def _packet(objective="评估 12 个月现金流断流风险与两选项的机会成本"):
    return build_expert_task_packet(
        task_id="task-eco-1",
        expert_id="economist",
        objective=objective,
        decision=DECISION,
        plan=PLAN,
        context_version=1,
    )


def test_packet_factory_uses_profile_role_contract():
    packet = _packet()
    assert packet.expert_id == "economist"
    rc = packet.role_contract
    assert rc["lens_title"] == "成本与回报"
    assert rc["display_name"] == "经济学家"
    assert rc["forbidden_inferences"]  # 经济学家带禁止编造清单
    assert packet.allowed_tools == ["web_search", "calculator", "memory_search"]
    assert packet.relevant_user_facts == ["当前年薪 60 万", "有 150 万存款"]


def json_dumps_lower(obj):
    import json
    return json.dumps(obj, ensure_ascii=False).lower()


def test_packet_rejects_unknown_expert_and_vague_objective():
    with pytest.raises(Exception):
        build_expert_task_packet(
            task_id="t", expert_id="artist", objective="分析一下这个那个的问题",
            decision=DECISION, plan=PLAN, context_version=1,
        )
    with pytest.raises(Exception):
        _packet(objective="请分析这个问题")


def test_context_packet_hash_and_isolation():
    packet = _packet()
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    assert ctx.compiled_for == "economist"
    assert ctx.context_version == 1
    assert len(ctx.content_sha256) == 64
    # 相同输入 → 相同哈希；任何内容变化 → 哈希变化
    ctx2 = compile_child_context(_packet(), decision=DECISION, plan=PLAN)
    assert ctx2.content_sha256 == ctx.content_sha256
    ctx3 = compile_child_context(
        _packet(), decision=DECISION.model_copy(update={"question": "换一个问题标题"}),
        plan=PLAN,
    )
    assert ctx3.content_sha256 != ctx.content_sha256
    # 隔离：上下文里没有其他专家的任何内容（无 transcript 字段）
    blob = json_dumps_lower(ctx.sections)
    for other in ("psychologist", "historian", "transcript", "analysis_points"):
        assert other not in blob, f"子 Agent 上下文泄漏: {other}"


def test_child_prompt_contains_contract_not_history():
    packet = _packet()
    ctx = compile_child_context(packet, decision=DECISION, plan=PLAN)
    prompt = render_child_prompt(packet, ctx)
    assert "现金流最多撑 12 个月" in prompt
    assert "SubAgentResult" in prompt
    assert "silent" in prompt
    assert render_child_finalize_prompt(packet).startswith(f"任务 {packet.task_id} 的轮次预算")


def test_root_observation_whitelist_only():
    results = [
        SubAgentResult(
            task_id="task-eco-1", agent_id="economist", status="completed",
            stance="cautious", judgment="现金流可支撑但容错率低",
            analysis_points=["细节1", "细节2"], unknowns=["期权条款"],
            confidence=0.7,
        ),
        SubAgentResult(task_id="task-x", agent_id="economist", status="failed"),
    ]
    obs = root_observation(results)
    import json
    blob = json.dumps(obs, ensure_ascii=False)
    assert obs["summary"] == {"total": 2, "completed": 1, "partial": 0,
                              "silent": 0, "failed": 1}
    # 白名单外字段不得出现（analysis_points / assumptions / reversal_conditions）
    for banned in ("analysis_points", "assumptions", "reversal_conditions",
                   "questions_for_other_agents", "completion"):
        assert banned not in blob
    # 判断与未知项保留（Root 需要它们做综合）
    assert "现金流可支撑" in blob
