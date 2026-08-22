"""Codex → GLM 网关的接线（生成 CODEX_HOME 与 config.toml）。

Codex 的 model_providers 只认 Responses API；把 provider 指向本地网关：

    [model_providers.shenmo-glm]
    name = "Shenmo GLM Gateway"
    base_url = "http://127.0.0.1:8098/v1"
    env_key = "SHENMO_GLM_KEY"
    wire_api = "responses"

launch_args_override 路径下 --config 不生效（Phase 2-C 实测），
配置因此写入独立 CODEX_HOME 的 config.toml，经 CodexConfig(env=...)
注入。env_key 只用于鉴权头存在性，网关不校验其值。
"""
from __future__ import annotations

import os
from pathlib import Path

from app.config import get_settings

PROVIDER_ID = "shenmo-glm"


def _config_toml(*, gateway_url: str, model: str) -> str:
    return f"""# 由 app.glm_gateway.provider 自动生成——手工改动会被覆盖
model = "{model}"
model_provider = "{PROVIDER_ID}"

[model_providers.{PROVIDER_ID}]
name = "Shenmo GLM Gateway"
base_url = "{gateway_url.rstrip('/')}/v1"
env_key = "SHENMO_GLM_KEY"
wire_api = "responses"
"""


def write_codex_home(
    *,
    home: str | Path,
    gateway_url: str | None = None,
    model: str | None = None,
) -> Path:
    """生成（或刷新）一个指向 GLM 网关的 CODEX_HOME。幂等。"""
    settings = get_settings()
    gateway_url = gateway_url or settings.glm_gateway_url
    model = model or settings.glm_model
    home_path = Path(home)
    home_path.mkdir(parents=True, exist_ok=True)
    (home_path / "config.toml").write_text(
        _config_toml(gateway_url=gateway_url, model=model), encoding="utf-8"
    )
    return home_path


def build_glm_codex_env(
    *, codex_bin: str, gateway_url: str | None = None, model: str | None = None,
    home: str | Path | None = None,
) -> dict:
    """构造指向自建二进制 + GLM 网关的 CodexConfig。

    返回可直接传给 openai_codex.CodexConfig 的构造参数 dict：
        CodexConfig(**build_glm_codex_env(codex_bin=BIN))
    """
    settings = get_settings()
    home = Path(home) if home else (
        Path.home() / ".shenmo" / "codex-home-glm"
    )
    write_codex_home(home=home, gateway_url=gateway_url, model=model)
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    env["SHENMO_GLM_KEY"] = settings.glm_api_key  # 网关不校验，占位鉴权头
    return {
        "launch_args_override": (codex_bin, "--listen", "stdio://"),
        "env": {"CODEX_HOME": env["CODEX_HOME"],
                "SHENMO_GLM_KEY": "set"},
        "client_name": "shenmo_harness",
    }
