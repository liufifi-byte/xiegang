"""GLM → Codex 网关包。

用法：
  python -m app.glm_gateway          # 启动网关（127.0.0.1:8098）
  Codex 侧：DECISION_RUNTIME 相关代码用 build_glm_codex_env() 生成
  CODEX_HOME + model_providers 配置（见 provider.py）。
"""
from app.glm_gateway.provider import build_glm_codex_env, write_codex_home

__all__ = ["build_glm_codex_env", "write_codex_home"]
