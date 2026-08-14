"""测试 P0 修复：用户不再看到"该专家本轮调研未完成。"
和"本轮未及时回应，会议继续进行。"两条文案。

L1+L2 改动后这两条文案被替换为：
  - 调研失败 → 人格化降级发言（基于 profile.professional_identity）
  - 辩论失败 → "本轮时间或网络受限，未发表新的质疑或补充，维持此前X的立场。"

判断方法：用 ast 解析，只在真实 string literal（给运行时字段赋值的字符串）
里扫描。注释和 docstring 里的引用不算（描述历史不算复活）。
"""
import ast
from pathlib import Path

import pytest

TARGET_FILES = [
    "app/graph/nodes/parallel_research.py",
    "app/graph/nodes/round_debate.py",
    "app/graph/nodes/summary.py",
    "app/agents/trace.py",
]

STALE_TEXTS = [
    "该专家本轮调研未完成",
    "本轮未及时回应",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _collect_string_literals(node: ast.AST) -> list[str]:
    """递归收集 AST 中所有真实字符串字面值（Constant str）。

    不包含：docstring（函数/类/模块的第一条 expr stmt str）—— 我们单独跳过。
    """
    literals: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            literals.append(child.value)
    return literals


def _is_docstring(node: ast.AST) -> bool:
    """是否是函数/类/模块的第一条表达式语句字符串。"""
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
        return False
    body = getattr(node, "body", None)
    if not body or not isinstance(body[0], ast.Expr):
        return False
    val = body[0].value
    return isinstance(val, ast.Constant) and isinstance(val.value, str)


@pytest.mark.parametrize("stale_text", STALE_TEXTS)
def test_stale_text_not_in_runtime_string_literals(stale_text):
    """死字文案不得出现在任何"会被运行时读到"的字符串字面值里。

    docstring 里的解释文字允许（用于说明这个 fix 的来由），但
    实际数据流里的字符串（dict value / function return / format arg）不允许。
    """
    repo = _repo_root()
    hits = []
    for rel in TARGET_FILES:
        path = repo / rel
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src, filename=str(path))
        except SyntaxError:
            continue

        # 收集所有 docstring 节点的 _id_以便后续 walk 时 skip
        docstring_node_ids: set[int] = set()
        for sub in ast.walk(tree):
            if _is_docstring(sub):
                docstring_node_ids.add(id(sub.body[0].value))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if not isinstance(node.value, str):
                continue
            if id(node) in docstring_node_ids:
                # 这是 docstring——允许引用历史文案做解释
                continue
            if stale_text in node.value:
                # 命中真实字符串字面值
                hits.append(f"{rel}:line ? 含「{stale_text}」字面值")

    assert not hits, (
        f"死字文案「{stale_text}」仍以运行时字符串字面值形式存在：\n"
        + "\n".join(hits)
    )


def test_degraded_research_text_uses_profile_persona():
    """parallel_research 替换后的文案应当引用 profile 的视角描述。"""
    path = _repo_root() / "app/graph/nodes/parallel_research.py"
    text = path.read_text(encoding="utf-8")
    assert "时间或资源受限" in text, "应使用更柔和的降级文案"
    assert "建议你在阅读其他专家观点" in text
    assert "get_expert_profile" in text


def test_degraded_debate_uses_speaker_self_message():
    """round_debate 替换后的文案应当是 speaker 自己的话，不再是 system 消息。

    之前用 type="debate_error" + speaker="system"；改为 type="debate_degraded"
    + speaker=该议员本人。这样 assess_third_round 不会强制开第三轮。
    """
    path = _repo_root() / "app/graph/nodes/round_debate.py"
    text = path.read_text(encoding="utf-8")
    assert "debate_degraded" in text
    assert "维持此前" in text
    assert "时间或网络受限" in text


def test_council_rounds_assess_third_round_unaffected_by_degraded():
    """assess_third_round 检测类型不该被新 degraded 类型干扰。"""
    path = _repo_root() / "app/graph/nodes/council_rounds.py"
    text = path.read_text(encoding="utf-8")
    assert '"debate_error"' in text
    assert "debate_degraded" not in text
