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

    # Supabase
    supabase_url: str
    supabase_key: str
    supabase_db_url: str

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
