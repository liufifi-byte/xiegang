"""parallel_research 节点测试（V1-S2 async 版）。"""
import asyncio

from app.graph.nodes.parallel_research import (
    MEMBER_TOOLS,
    _load_prompt_template,
    parallel_research,
)


def test_member_tools_has_5_members():
    assert len(MEMBER_TOOLS) == 5
    assert set(MEMBER_TOOLS.keys()) == {"suan", "chen", "ji", "nian", "fan"}


def test_each_member_has_web_search():
    for mid, tools in MEMBER_TOOLS.items():
        tool_names = [t.__name__ for t in tools]
        assert "web_search" in tool_names, f"{mid} 缺少 web_search"


def test_each_member_has_exactly_2_tools():
    for mid, tools in MEMBER_TOOLS.items():
        assert len(tools) == 2, f"{mid} 工具数 != 2: {tools}"


def test_load_prompt_template_suan():
    tpl = _load_prompt_template("suan")
    assert "老算" in tpl
    assert "期望值" in tpl


def test_load_prompt_template_all_5_members():
    """5 个议员的 prompt 都能加载 + 含 few-shot 第 6 层。"""
    for mid in ["suan", "chen", "ji", "nian", "fan"]:
        tpl = _load_prompt_template(mid)
        assert "第 6 层" in tpl, f"{mid} 缺少 few-shot 层"
        assert "{topic}" in tpl, f"{mid} prompt 缺 topic 占位符"
        assert "{variables}" in tpl, f"{mid} prompt 缺 variables 占位符"


def test_parallel_research_returns_5_members(monkeypatch):
    """Mock 异步调研，验证聚合逻辑。"""
    from app.graph.nodes import parallel_research as pr_module
    from app.schemas.member import Evidence, MemberStatement

    async def mock_research(mid, topic, variables, parliament_id=None, memories="（暂无相关历史记忆）"):
        stmt = MemberStatement(
            member_id=mid,
            stance="neutral",
            confidence=0.5,
            evidence=[Evidence(claim="mock", source="test", strength="medium")],
            reasoning_summary=f"{mid} mock reply",
        )
        return stmt, [{"tool": "web_search", "ok": True}]

    monkeypatch.setattr(pr_module, "_research_for_member_async", mock_research)

    state = {
        "topic": "买房",
        "briefing": {"key_variables": ["city", "price"]},
        "transcript": [],
    }
    result = asyncio.run(parallel_research(state))

    assert len(result["members"]) == 5
    assert "suan" in result["members"]
    assert "fan" in result["members"]
    # 初轮发言应该入 transcript
    assert len(result["transcript"]) == 5
    assert result["transcript"][0]["type"] == "research"


def test_parallel_research_handles_member_error(monkeypatch):
    """单个议员炸了不该拖垮整个议会。"""
    from app.graph.nodes import parallel_research as pr_module
    from app.schemas.member import Evidence, MemberStatement

    async def mock_research(mid, topic, variables, parliament_id=None, memories="（暂无相关历史记忆）"):
        if mid == "suan":
            raise RuntimeError("simulated LLM error")
        stmt = MemberStatement(
            member_id=mid, stance="for", confidence=0.7,
            evidence=[Evidence(claim="x", source="y", strength="high")],
            reasoning_summary=f"{mid} ok",
        )
        return stmt, [{"tool": "web_search", "ok": True}]

    monkeypatch.setattr(pr_module, "_research_for_member_async", mock_research)

    state = {"topic": "x", "briefing": {"key_variables": []}, "transcript": []}
    result = asyncio.run(parallel_research(state))

    # 5 个都要有结果
    assert len(result["members"]) == 5
    # 失败的议员有 ERROR 标记
    assert "ERROR" in result["members"]["suan"]["reasoning_summary"]
    assert result["members"]["suan"]["stance"] == "neutral"
    assert result["members"]["suan"]["confidence"] == 0.0
    # 其他议员正常
    assert result["members"]["chen"]["stance"] == "for"


def test_parallel_research_preserves_existing_transcript(monkeypatch):
    """已有的 transcript 不应该被清空。"""
    from app.graph.nodes import parallel_research as pr_module
    from app.schemas.member import MemberStatement

    async def mock_research(mid, topic, variables, parliament_id=None, memories="（暂无相关历史记忆）"):
        stmt = MemberStatement(
            member_id=mid, stance="neutral", confidence=0.5,
            evidence=[], reasoning_summary=f"{mid}",
        )
        return stmt, []

    monkeypatch.setattr(pr_module, "_research_for_member_async", mock_research)

    # 主调度曾经在 transcript 里写过解析结果
    existing = [{"type": "parse", "content": "topic parsed"}]
    state = {
        "topic": "x",
        "briefing": {"key_variables": []},
        "transcript": existing,
    }
    result = asyncio.run(parallel_research(state))
    # 老的还在
    assert any(e["type"] == "parse" for e in result["transcript"])
    # 新的 5 条也在
    research_entries = [e for e in result["transcript"] if e["type"] == "research"]
    assert len(research_entries) == 5


def test_semaphore_can_be_created():
    """并发模块可加载。"""
    from app.llm_concurrency import get_llm_semaphore
    sem = get_llm_semaphore()
    assert sem is not None


def test_parallel_research_runs_selected_expert_agents(monkeypatch):
    """Selected professional IDs switch the node to the ReAct agent path."""
    from app.graph.nodes import parallel_research as pr_module

    async def fake_run(expert_id, state, registry, checkpointer=None):
        return {
            "ok": True,
            "expert_id": expert_id,
            "state": {
                "final_result": {
                    "agent_id": expert_id,
                    "stance": "for",
                    "confidence": 0.7,
                    "thesis": f"{expert_id} conclusion",
                    "evidence_refs": [],
                    "assumptions": [],
                    "unknowns": [],
                    "reversal_conditions": ["条件改变"],
                    "next_actions": [],
                    "tool_calls_used": 0,
                    "degraded": False,
                    "degradation_reason": None,
                },
                "evidence_ledger": [],
                "safe_action_history": [],
                "trace_events": [],
                "metrics": {"reason_steps": 1, "tool_calls": 0},
            },
        }

    monkeypatch.setattr(pr_module, "_run_one_expert_safe", fake_run)
    selected = [
        "mathematician",
        "economist",
        "psychologist",
        "artist",
        "politician",
    ]
    state = {
        "topic": "要不要接 Offer",
        "category": "offer",
        "briefing": {"summary": "Offer", "key_variables": ["薪资"]},
        "selected_expert_ids": selected,
        "transcript": [],
        "tool_calls": [],
        "agent_traces": [],
    }
    result = asyncio.run(parallel_research(state))
    assert set(result["members"]) == set(selected)
    assert len(result["transcript"]) == 5
    assert result["research_degraded"] is False


def test_parallel_agent_research_isolates_single_failure(monkeypatch):
    from app.graph.nodes import parallel_research as pr_module

    async def fake_run(expert_id, state, registry, checkpointer=None):
        if expert_id == "artist":
            return {"ok": False, "expert_id": expert_id, "error": "timeout"}
        return {
            "ok": True,
            "expert_id": expert_id,
            "state": {
                "final_result": {
                    "agent_id": expert_id,
                    "stance": "neutral",
                    "confidence": 0.5,
                    "thesis": "ok",
                    "evidence_refs": [],
                    "assumptions": [],
                    "unknowns": [],
                    "reversal_conditions": ["新信息"],
                    "next_actions": [],
                    "tool_calls_used": 0,
                    "degraded": False,
                    "degradation_reason": None,
                },
                "evidence_ledger": [],
                "safe_action_history": [],
                "trace_events": [],
                "metrics": {},
            },
        }

    monkeypatch.setattr(pr_module, "_run_one_expert_safe", fake_run)
    state = {
        "topic": "x",
        "category": "general",
        "briefing": {},
        "selected_expert_ids": [
            "mathematician", "economist", "psychologist", "artist", "politician"
        ],
        "transcript": [],
        "tool_calls": [],
        "agent_traces": [],
    }
    result = asyncio.run(parallel_research(state))
    assert result["members"]["artist"]["degraded"] is True
    assert result["members"]["economist"]["confidence"] == 0.5
    assert result["research_degraded"] is False
