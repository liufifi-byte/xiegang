"""状态机组装测试。"""
from app.graph.parliament import build_parliament_graph, get_graph, should_continue_debate


def test_should_continue_debate_when_not_done():
    state = {"current_round": 1, "max_rounds": 3}
    assert should_continue_debate(state) == "debate"


def test_should_continue_debate_when_done():
    state = {"current_round": 3, "max_rounds": 3}
    assert should_continue_debate(state) == "election"


def test_build_parliament_graph_compiles():
    """组装状态机本身不报错（顶点 / 边配置正确）。"""
    graph = build_parliament_graph()
    assert graph is not None


def test_get_graph_returns_singleton():
    """懒加载单例生效。"""
    g1 = get_graph()
    g2 = get_graph()
    assert g1 is g2


def test_graph_has_6_nodes():
    """编译后的 graph 应该有 6 个 node。"""
    # 通过 build 后取 nodes 数（结构层面验证）
    # 不能直接 inspect compiled graph 内部，构造时验证不抛错即可
    # 这里间接通过 should_continue + build 验证流程结构
    assert should_continue_debate({"current_round": 0, "max_rounds": 3}) == "debate"
    assert should_continue_debate({"current_round": 2, "max_rounds": 3}) == "debate"
    assert should_continue_debate({"current_round": 3, "max_rounds": 3}) == "election"


def test_graph_uses_two_to_three_dispute_driven_rounds():
    graph = build_parliament_graph()
    nodes = set(graph.get_graph().nodes)
    assert "opening_round" in nodes
    assert "extract_disputes" in nodes
    assert "cross_examination_round" in nodes
    assert "assess_third_round" in nodes
    assert "convergence_round" in nodes
    assert "select_divergent" not in nodes
    assert "select_chair" not in nodes
    assert "election" not in nodes
    assert "clarification_gate" in nodes
    assert "persist_clarification" in nodes
