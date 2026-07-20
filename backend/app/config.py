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


@lru_cache
def get_settings() -> Settings:
    return Settings()
