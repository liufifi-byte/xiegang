"""Checkpointer 单元测试（V2-S1）。

真实 Postgres 连接在测试环境不打，只验证：
- 没有 checkpointer 时 build 返回的图能正常 compile
- get_checkpointer 函数 import 路径正确
"""
from app.graph.parliament import build_parliament_graph, should_continue_debate


def test_should_continue_debate_when_not_done():
    state = {"current_round": 1, "max_rounds": 3}
    assert should_continue_debate(state) == "debate"


def test_should_continue_debate_when_done():
    state = {"current_round": 3, "max_rounds": 3}
    assert should_continue_debate(state) == "election"


def test_build_parliament_graph_without_checkpointer():
    """V2-S1: 不传 checkpointer 时走 in-memory 模式。"""
    graph = build_parliament_graph(checkpointer=None)
    assert graph is not None


def test_build_parliament_graph_with_memory_saver():
    """V2-S1: 传 MemorySaver 走 checkpoint 模式（不连真 DB）。"""
    from langgraph.checkpoint.memory import MemorySaver
    graph = build_parliament_graph(checkpointer=MemorySaver())
    assert graph is not None


def test_async_postgres_saver_import_path():
    """V2-S1: 验证 AsyncPostgresSaver import 路径可用。"""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    assert AsyncPostgresSaver is not None


def test_checkpointer_module_importable():
    """V2-S1: 验证 app.checkpointer 模块可 import。"""
    from app.checkpointer import get_checkpointer
    assert callable(get_checkpointer)
