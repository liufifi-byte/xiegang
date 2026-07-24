"""LangGraph StateGraph for the bounded professional council workflow.

流程：
    parse_topic → select_experts → research → select_divergent
    → cross_examination → select_chair → summary → archive

V2-S1: 接受 checkpointer 参数，配合 thread_id 实现跨重启续盘。
"""
from langgraph.graph import END, StateGraph

from app.graph.nodes.archive import run_archive
from app.graph.nodes.cross_examination import run_cross_examination
from app.graph.nodes.parse_topic import parse_topic
from app.graph.nodes.parallel_research import parallel_research
from app.graph.nodes.select_chair import select_chair
from app.graph.nodes.select_divergent_experts import select_divergent_experts
from app.graph.nodes.select_experts import select_experts
from app.graph.nodes.summary import run_summary
from app.graph.state import ParliamentState


def should_continue_debate(state: dict) -> str:
    """辩论是否继续：跑满 max_rounds 进入选举。"""
    if state["current_round"] >= state["max_rounds"]:
        return "election"
    return "debate"


def build_parliament_graph(checkpointer=None):
    """组装状态机。

    Args:
        checkpointer: 可选 LangGraph checkpointer。
                      V2-S1 之后从 get_checkpointer() 拿 AsyncPostgresSaver。
                      测试时默认 None（in-memory）。
    """
    workflow = StateGraph(ParliamentState)

    workflow.add_node("parse_topic", parse_topic)
    workflow.add_node("research", parallel_research)
    workflow.add_node("select_experts", select_experts)
    workflow.add_node("select_divergent", select_divergent_experts)
    workflow.add_node("cross_examination", run_cross_examination)
    workflow.add_node("select_chair", select_chair)
    workflow.add_node("summary", run_summary)
    workflow.add_node("archive", run_archive)

    workflow.set_entry_point("parse_topic")
    workflow.add_edge("parse_topic", "select_experts")
    workflow.add_edge("select_experts", "research")
    workflow.add_edge("research", "select_divergent")
    workflow.add_edge("select_divergent", "cross_examination")
    workflow.add_edge("cross_examination", "select_chair")
    workflow.add_edge("select_chair", "summary")
    workflow.add_edge("summary", "archive")
    workflow.add_edge("archive", END)

    if checkpointer is None:
        # 测试用：in-memory
        return workflow.compile()

    return workflow.compile(checkpointer=checkpointer)


_graph = None


def get_graph():
    """懒加载编译好的状态机（V2 之前用 in-memory）。

    V2-S1 之后调用方应该用 get_graph_with_checkpointer() 而不是这个。
    """
    global _graph
    if _graph is None:
        _graph = build_parliament_graph()
    return _graph


async def get_graph_with_checkpointer():
    """V2-S1: 带 Postgres checkpointer 的 graph（生产用）。

    首次调用时初始化 checkpointer + setup()。
    """
    from app.config import get_settings

    if not get_settings().langgraph_checkpointer_enabled:
        return get_graph()

    from app.checkpointer import get_checkpointer

    checkpointer = await get_checkpointer()
    return build_parliament_graph(checkpointer=checkpointer)
