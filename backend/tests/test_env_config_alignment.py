"""测试 config.py 默认值 vs .env.example 三方一致性。

避免再出现"线上 .env 旧值 / .env.example 半新值 / config.py 默认值"三方漂移导致
部署后议会行为不一致的问题（之前 agent_total_timeout 三套值 120/180/180，
parliament_total_timeout 三套值 720/1200/480）。
"""
from pathlib import Path

from app.config import Settings


def _read_env_example():
    """解析 .env.example 所有 KEY=VALUE 对。"""
    path = Path(__file__).resolve().parent.parent.parent / ".env.example"
    pairs = {}
    if not path.exists():
        return pairs
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value.strip()
    return pairs


def test_agent_total_timeout_default():
    """默认值应 ≥ 180s（之前 120s 不够造成"未完成"）。"""
    f = Settings.model_fields["agent_total_timeout_seconds"]
    assert f.default >= 180, f"agent_total_timeout 默认值 {f.default} 不够（应≥180）"


def test_parliament_total_timeout_default():
    """完整议会需 14-20 分钟，默认值应 ≥ 1200s。"""
    f = Settings.model_fields["parliament_total_timeout"]
    assert f.default >= 1200, f"parliament_total_timeout 默认值 {f.default} 太紧"


def test_parliament_stale_seconds_greater_than_total_timeout():
    """stale 阈值必须 > 议会总耗时，避免正在跑的议会误判 orphan。

    不然正常议会跑一半就被 _reclaim_stale_tasks 标 failed。
    """
    s = Settings()
    assert s.parliament_stale_seconds >= s.parliament_total_timeout, (
        f"stale {s.parliament_stale_seconds}s < total {s.parliament_total_timeout}s "
        "→ 正常议会会被 reclaim 误杀"
    )


def test_env_example_matches_config_defaults():
    """env.example 里设置的超时值应当与 config.py 默认值一致。"""
    env = _read_env_example()
    fields = Settings.model_fields

    cross_check = [
        ("PARLIAMENT_TOTAL_TIMEOUT", "parliament_total_timeout"),
        ("AGENT_TOTAL_TIMEOUT_SECONDS", "agent_total_timeout_seconds"),
        ("PARLIAMENT_STALE_SECONDS", "parliament_stale_seconds"),
        ("GLM_REQUEST_TIMEOUT_SECONDS", "glm_request_timeout_seconds"),
    ]
    mismatches = []
    for env_key, field_name in cross_check:
        env_value = env.get(env_key)
        config_default = fields[field_name].default
        if env_value is None:
            mismatches.append(f"{env_key} 缺失")
            continue
        try:
            env_int = int(env_value)
        except ValueError:
            try:
                env_int = float(env_value)
            except ValueError:
                mismatches.append(f"{env_key}={env_value} 不是数字")
                continue
        if env_int != config_default:
            mismatches.append(
                f"{env_key}={env_int} ≠ config.py 默认值 {config_default}"
            )
    assert not mismatches, (
        "env.example 与 config.py 默认值不一致（部署时会爆配置漂移）：\n"
        + "\n".join(mismatches)
    )
