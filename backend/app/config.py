from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 从 council 根目录加载 .env（不是 backend 目录）
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    """所有配置从 .env 加载。"""

    model_config = SettingsConfigDict(env_file=str(_ENV_PATH), extra="ignore")

    # GLM-5 (智谱)
    glm_api_key: str
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_model: str = "glm-5"
    glm_embedding_model: str = "embedding-3"
    glm_request_timeout_seconds: float = 45.0
    glm_max_retries: int = 0

    # Supabase
    supabase_url: str
    supabase_key: str
    supabase_db_url: str
    # P0-S2: langgraph checkpointer 用直连（绕 PgBouncer 不支持 prepared statements）
    supabase_db_url_direct: str = ""

    # Tavily
    tavily_api_key: str

    # 应用配置
    app_env: str = "development"
    log_level: str = "INFO"

    # P0-S1.1 限流配置
    rate_limit_ip_per_minute: int = 2  # 单 IP 每分钟创建议会数
    rate_limit_device_daily: int = 10  # 单 device 每天议会数

    # P0-S1.4 CORS
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001"

    # P0-S1.2 议会总超时（秒）
    parliament_total_timeout: int = 480  # 8 分钟

    # Expert ReAct Agent 执行预算
    agent_max_reason_steps: int = 3
    agent_max_tool_calls: int = 1
    agent_tool_timeout_seconds: int = 15
    agent_total_timeout_seconds: int = 120
    agent_research_node_timeout_seconds: int = 135
    agent_max_observation_chars: int = 4000
    agent_max_evidence: int = 3
    expert_research_mode: str = "react_agent"

    # LangGraph checkpoint（本地联调可通过环境变量关闭，生产默认开启）
    langgraph_checkpointer_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
