"""真实 Codex App Server 最小集成测试（Phase 2-C）。

与 fake-client 单元测试互补：这里启动**真实的自建二进制**
（CODEX_FORK_PIN 记录的深墨 Fork 构建产物），验证我们的
CodexAgentRuntime 适配器能走通 initialize → thread/start →
close → thread/resume 全链路（spec §20 Phase 2 最小集成验收）。

不跑 turn：真实模型调用需要 OpenAI 凭据，且 Phase 2 验收只要求
Runtime 连通性。轮次级行为由 FakeAgentRuntime 单元测试覆盖。

跳过条件（两者任一）：本地无自建二进制；openai-codex SDK 未安装。
"""
import os

import pytest

from app.decision_harness.codex_runtime import CodexAgentRuntime

CODEX_BIN = os.environ.get(
    "CODEX_BIN",
    "/Users/xinyiliu/Desktop/codex-fork/codex-rs/target/debug/codex-app-server",
)

pytestmark = [
    pytest.mark.skipif(
        not os.path.exists(CODEX_BIN),
        reason=f"自建 codex-app-server 不存在: {CODEX_BIN}（见 CODEX_FORK_PIN）",
    ),
    pytest.mark.skipif(
        os.environ.get("SKIP_REAL_CODEX", "").lower() in ("1", "true", "yes"),
        reason="SKIP_REAL_CODEX=1",
    ),
]


def _sdk_available() -> bool:
    try:
        import openai_codex  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture
def require_sdk():
    if not _sdk_available():
        pytest.skip("openai-codex SDK 未安装（pip install -e '.[codex]'）")


async def test_real_app_server_thread_lifecycle(require_sdk):
    rt = CodexAgentRuntime(codex_bin=CODEX_BIN, model="gpt-5.2")
    session = "itest-main"
    try:
        await rt.start_session(session, instructions="集成测试：决策主席占位指令。")
        thread_id = rt.runtime_thread_id(session)
        assert thread_id, "thread/start 应返回线程 id"
        await rt.close_session(session)
        assert rt.runtime_thread_id(session) is None

        # 协议事实（实测）：thread/start 是惰性的——零 turn 时 rollout 不落盘，
        # thread/resume 会报 "no rollout found"。真实运行的首个 turn 终结时
        # 才 flush（勘察 §8/§10）。因此此处断言领域错误而非成功恢复；
        # 带 turn 的恢复链路由 worker 集成（有凭据环境）验证。
        from openai_codex.errors import InvalidRequestError

        with pytest.raises(InvalidRequestError, match="no rollout found"):
            await rt.resume_session(session, runtime_thread_id=thread_id)
    finally:
        await rt.aclose()


async def test_real_app_server_interrupt_without_live_turn(require_sdk):
    """无在途轮次时 interrupt 幂等 no-op（spec §7.4）。"""
    rt = CodexAgentRuntime(codex_bin=CODEX_BIN, model="gpt-5.2")
    session = "itest-idem"
    try:
        await rt.start_session(session, instructions="集成测试")
        await rt.interrupt_turn(session)  # 不应抛错
        await rt.close_session(session)
    finally:
        await rt.aclose()
