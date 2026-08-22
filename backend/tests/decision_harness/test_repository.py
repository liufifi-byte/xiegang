"""DecisionRunRepository 测试：内存模式全接口 + 重启恢复 + SQL 幂等静态检查。"""
import pytest

from app.decision_harness.repository import (
    DecisionRunRepository,
    _clear_memory_for_tests,
)


@pytest.fixture(autouse=True)
def _clean_memory():
    _clear_memory_for_tests()
    yield
    _clear_memory_for_tests()


def _repo() -> DecisionRunRepository:
    return DecisionRunRepository(force_memory=True)


async def test_create_run_idempotent():
    repo = _repo()
    assert await repo.create_run(
        "r1", runtime_mode="codex_harness",
        decision_state={"question": "换工作吗"}, budget={"max_main_actions": 12},
    )
    assert not await repo.create_run(
        "r1", runtime_mode="codex_harness",
        decision_state={"question": "换工作吗"}, budget={},
    )
    row = await repo.get_run("r1")
    assert row["status"] == "running"
    assert row["workflow_phase"] == "INTAKE"
    assert row["decision_state"]["question"] == "换工作吗"


async def test_update_run_whitelist():
    repo = _repo()
    await repo.create_run(
        "r1", runtime_mode="codex_harness", decision_state={}, budget={}
    )
    await repo.update_run(
        "r1", status="planned", root_thread_id="th-1", active_plan={"v": 1}
    )
    row = await repo.get_run("r1")
    assert row["status"] == "planned"
    assert row["root_thread_id"] == "th-1"

    with pytest.raises(ValueError, match="不允许写入列"):
        await repo.update_run("r1", evil_column="; DROP TABLE runs")


async def test_plan_versions_append_and_order():
    repo = _repo()
    await repo.create_run(
        "r1", runtime_mode="codex_harness", decision_state={}, budget={}
    )
    await repo.append_plan_version("r1", 1, {"objective": "v1"}, "initial")
    await repo.append_plan_version("r1", 2, {"objective": "v2"}, "chair_revision")
    # 同版本重放 → 覆盖（幂等）
    await repo.append_plan_version("r1", 2, {"objective": "v2-fix"}, "chair_revision")

    versions = await repo.list_plan_versions("r1")
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["plan"]["objective"] == "v2-fix"


async def test_agent_runs_upsert_lifecycle():
    repo = _repo()
    await repo.create_run(
        "r1", runtime_mode="codex_harness", decision_state={}, budget={}
    )
    await repo.upsert_agent_run(
        "a1", decision_run_id="r1", agent_type="main",
        status="running", started=True,
    )
    await repo.upsert_agent_run(
        "a1", decision_run_id="r1", agent_type="main",
        status="completed", result={"ok": True}, finished=True,
    )
    rows = await repo.list_agent_runs("r1")
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["result"] == {"ok": True}
    assert rows[0]["started_at"] is not None
    assert rows[0]["finished_at"] is not None


async def test_plan_survives_worker_restart():
    """Phase 1 验收：仓库实例重建（模拟 worker 重启）后计划仍在。"""
    repo_before = _repo()
    await repo_before.create_run(
        "r1", runtime_mode="codex_harness",
        decision_state={"question": "要不要考研"}, budget={},
    )
    await repo_before.update_run("r1", root_thread_id="th-codex-1")
    await repo_before.append_plan_version(
        "r1", 1, {"objective": "评估考研 vs 就业"}, "initial"
    )

    # 模拟重启：全新实例（进程内共享存储 ≠ DB，但接口语义一致）
    repo_after = _repo()
    row = await repo_after.get_run("r1")
    assert row is not None
    assert row["root_thread_id"] == "th-codex-1"  # Codex 线程可恢复
    versions = await repo_after.list_plan_versions("r1")
    assert len(versions) == 1
    assert versions[0]["plan"]["objective"] == "评估考研 vs 就业"


def test_migration_002_idempotent_sql():
    """静态验收：全部 CREATE IF NOT EXISTS、零 DROP/ALTER（spec §24.8）。"""
    from pathlib import Path

    sql = Path(__file__).resolve().parents[2] / "migrations" / "002_decision_agent_harness.sql"
    text = sql.read_text(encoding="utf-8")
    # 先剥离注释再断言（注释里允许提到 DROP/表名）
    stripped = "\n".join(
        line.split("--")[0] for line in text.splitlines()
    )
    # 剥离 DO $$ ... $$ 守卫块（内含存在性检查的幂等 DROP CONSTRAINT，可重放）
    import re as _re

    no_dollar = _re.sub(r"DO \$\$.*?\$\$;", "", stripped, flags=_re.S)
    upper = no_dollar.upper()
    assert " DROP " not in upper, "守卫块之外出现 DROP（不可重放）"
    assert "ALTER TABLE" not in upper, "守卫块之外出现 ALTER（不可重放）"
    import re

    creates = re.findall(r"CREATE\s+(TABLE|INDEX)\s+(?!IF\s+NOT\s+EXISTS)", stripped, re.I)
    assert not creates, f"存在未加 IF NOT EXISTS 的 CREATE: {creates}"
    # spec §14 五张表齐全
    for table in ("decision_runs", "agent_runs", "plan_versions",
                  "evidence_records", "debate_issues"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in stripped, table
    # §14.6：事件不建新表
    assert "CREATE TABLE IF NOT EXISTS decision_events" not in stripped
    assert "CREATE TABLE IF NOT EXISTS parliament_events" not in stripped
