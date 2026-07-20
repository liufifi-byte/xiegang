"""round_debate 节点测试（V1-S2 async 版）。"""
import asyncio

from app.graph.nodes.round_debate import (
    MEMBER_INFO,
    _format_history,
    run_debate_round,
)


def test_member_info_has_5():
    assert len(MEMBER_INFO) == 5
    assert set(MEMBER_INFO.keys()) == {"suan", "chen", "ji", "nian", "fan"}


def test_format_history_empty():
    result = _format_history([], "suan", 1)
    assert "第一轮" in result


def test_format_history_returns_strings():
    transcript = [
        {"speaker": "suan", "round": 0, "content": "数字看合算", "stance": "for"},
        {"speaker": "chen", "round": 1, "content": "但风险大", "stance": "against"},
    ]
    result = _format_history(transcript, "suan", 2)
    assert "suan" in result
    assert "chen" in result
    assert "数字看合算" in result
    assert "但风险大" in result


def test_format_history_truncates_long_content():
    long_text = "x" * 500
    transcript = [{"speaker": "suan", "round": 1, "content": long_text, "stance": "for"}]
    result = _format_history(transcript, "chen", 2)
    assert "..." in result
    assert len(result) < 600


def test_run_debate_round_increments_round(monkeypatch):
    """Mock LLM，验证轮次 + 5 议员发言。"""
    from app.graph.nodes import round_debate as rd_module
    from app.schemas.member import Evidence, MemberStatement

    async def mock_debate(mid, name, role, topic, variables, round_num, history, user_interrupt=None):
        return MemberStatement(
            member_id=mid, stance="for", confidence=0.7,
            evidence=[Evidence(claim="x", source="y", strength="high")],
            reasoning_summary="mock debate reply",
        )

    monkeypatch.setattr(rd_module, "_debate_one_member_async", mock_debate)

    state = {
        "topic": "买房",
        "briefing": {"key_variables": ["city"]},
        "members": {},
        "transcript": [],
        "tool_calls": [],
        "current_round": 0,
    }
    result = asyncio.run(run_debate_round(state))

    assert result["current_round"] == 1
    debates_r1 = [e for e in result["transcript"] if e["round"] == 1]
    assert len(debates_r1) == 5
    for entry in debates_r1:
        assert entry["type"] in ("debate", "debate_error", "user_interrupt")


def test_run_debate_round_preserves_existing_transcript(monkeypatch):
    """已有 transcript 不应被清空。"""
    from app.graph.nodes import round_debate as rd_module
    from app.schemas.member import MemberStatement

    async def mock_debate(mid, name, role, topic, variables, round_num, history, user_interrupt=None, parliament_id=None):
        return MemberStatement(
            member_id=mid, stance="for", confidence=0.7,
            evidence=[], reasoning_summary="reply",
        )

    monkeypatch.setattr(rd_module, "_debate_one_member_async", mock_debate)

    existing_research = [
        {"speaker": "suan", "round": 0, "type": "research",
         "content": "调研立场", "stance": "for", "confidence": 0.6},
    ]
    state = {
        "topic": "x", "briefing": {"key_variables": []},
        "members": {}, "tool_calls": [],
        "transcript": existing_research,
        "current_round": 0,
    }
    result = asyncio.run(run_debate_round(state))
    # 老的 research 仍在
    research_entries = [e for e in result["transcript"] if e["type"] == "research"]
    assert len(research_entries) == 1
    # 新的 5 条 debate 在
    debate_entries = [e for e in result["transcript"] if e["type"] == "debate"]
    assert len(debate_entries) == 5


def test_run_debate_round_tension_calculation(monkeypatch):
    """张力应该根据立场离散度算。"""
    from app.graph.nodes import round_debate as rd_module
    from app.schemas.member import MemberStatement

    # 5 个议员给不同立场：for, against, neutral, for, against
    stances_queue = iter(["for", "against", "neutral", "for", "against"])

    async def mock_debate(mid, name, role, topic, variables, round_num, history, user_interrupt=None, parliament_id=None):
        return MemberStatement(
            member_id=mid, stance=next(stances_queue), confidence=0.6,
            evidence=[], reasoning_summary="reply",
        )

    monkeypatch.setattr(rd_module, "_debate_one_member_async", mock_debate)

    state = {
        "topic": "x", "briefing": {"key_variables": []},
        "members": {}, "tool_calls": [], "transcript": [],
        "current_round": 0,
    }
    result = asyncio.run(run_debate_round(state))
    # 3 种 stance 都出现了 → 张力 = 3/3 = 1.0
    assert result["tension_score"] == 1.0


def test_run_debate_round_handles_member_error(monkeypatch):
    """单个议员炸了不影响其他议员。"""
    from app.graph.nodes import round_debate as rd_module
    from app.schemas.member import MemberStatement

    call_count = {"n": 0}

    async def mock_debate(mid, name, role, topic, variables, round_num, history, user_interrupt=None, parliament_id=None):
        call_count["n"] += 1
        if call_count["n"] == 1:  # 第一个议员炸
            raise RuntimeError("LLM error")
        return MemberStatement(
            member_id=mid, stance="for", confidence=0.6,
            evidence=[], reasoning_summary="reply",
        )

    monkeypatch.setattr(rd_module, "_debate_one_member_async", mock_debate)

    state = {
        "topic": "x", "briefing": {"key_variables": []},
        "members": {}, "tool_calls": [], "transcript": [],
        "current_round": 0,
    }
    result = asyncio.run(run_debate_round(state))
    # 5 条都在
    assert len([e for e in result["transcript"] if e["round"] == 1]) == 5
    # 至少有一个 ERROR 标记
    error_entries = [e for e in result["transcript"] if e["type"] == "debate_error"]
    assert len(error_entries) == 1


def test_run_debate_round_injects_user_interrupt(monkeypatch):
    """V1-S3: 有 user_interrupt → 写入 transcript + 注入到 prompt。"""
    from app.graph.nodes import round_debate as rd_module
    from app.schemas.member import MemberStatement

    captured_interrupt = {"v": None}

    async def mock_debate(mid, name, role, topic, variables, round_num, history, user_interrupt=None, parliament_id=None):
        captured_interrupt["v"] = user_interrupt
        return MemberStatement(
            member_id=mid, stance="for", confidence=0.6,
            evidence=[], reasoning_summary="reply",
        )

    async def mock_consume(parliament_id):
        return "预算 30w 上下" if parliament_id == "p-test" else None

    monkeypatch.setattr(rd_module, "_debate_one_member_async", mock_debate)
    monkeypatch.setattr(rd_module, "consume_user_interrupt", mock_consume)

    state = {
        "topic": "要不要买包", "briefing": {"key_variables": []},
        "members": {}, "tool_calls": [], "transcript": [],
        "current_round": 0,
        "parliament_id": "p-test",
    }
    result = asyncio.run(run_debate_round(state))

    # 插话应该传给议员
    assert captured_interrupt["v"] == "预算 30w 上下"
    # 插话应该写入 transcript（type=user_interrupt）
    interrupt_entries = [
        e for e in result["transcript"] if e["type"] == "user_interrupt"
    ]
    assert len(interrupt_entries) == 1
    assert interrupt_entries[0]["content"] == "预算 30w 上下"
    assert interrupt_entries[0]["speaker"] == "user"
