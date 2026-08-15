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
    # 延迟敏感轮次（辩论/选举/总结/会前流程）专用快速模型。
    # 实测（2026-08-15）：glm-5 开思考单次 ~28s，glm-5-turbo 关思考 ~2s；
    # 辩论轮 5 专家 × 3 轮 = 15 次调用，是超时降级重灾区。
    # 研究轮保 glm-5（多步推理质量优先）。新键：老 .env 无此变量时默认值直接生效。
    glm_fast_model: str = "glm-5-turbo"
    glm_fast_disable_thinking: bool = True
    glm_embedding_model: str = "embedding-3"
    glm_request_timeout_seconds: float = 180.0
    glm_max_retries: int = 0

    # Supabase
    supabase_url: str
    supabase_key: str
    supabase_db_url: str
    # P0-S2: langgraph checkpointer 用直连（绕 PgBouncer 不支持 prepared statements）
    supabase_db_url_direct: str = ""

    # Independent GLM Web Search API
    web_search_engine: str = "search_std"
    web_search_timeout_seconds: float = 15.0
    # require_fresh=True 时本地丢弃早于此年份的结果（默认去年）。
    # GLM 服务端 oneYear 过滤不严格，会漏进 2023/2024 旧文，靠这里再砍一刀。
    web_search_min_publish_year: int = 2025
    # Kept optional only so older local .env files continue to load.
    tavily_api_key: str = ""

    # 应用配置
    app_env: str = "development"
    log_level: str = "INFO"

    # P0-S1.1 限流配置
    rate_limit_ip_per_minute: int = 2  # 单 IP 每分钟创建议会数
    rate_limit_device_daily: int = 10  # 单 device 每天议会数

    # P0-S1.4 CORS
    cors_origins: str = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001"

    # 议会总超时（秒）。完整议会跑 5 Agent research + 3 轮 debate + 选举 + 总结
    # 实测稳定 runtime 是 14-20 分钟，720s 不够经常 fail。改 1200s 留充分余量。
    parliament_total_timeout: int = 1200

    # Expert ReAct Agent 执行预算
    # max_reason_steps：每个 Agent 的推理步数。ReAct 完整链 ≈ reason→tool→observe→reason→finish 共 5 步，
    # 3 步会在第 2 次 reason 触底 → AgentBudgetExceeded → fallback。改 5 覆盖典型链。
    agent_max_reason_steps: int = 5
    # max_tool_calls：1 太严格——专家常需 2 次搜索（如核心数据+补充政策）。
    # 改 2 覆盖大多数场景，同时不至于显著拖累总耗时。
    agent_max_tool_calls: int = 2
    agent_tool_timeout_seconds: int = 15
    # 单 Agent 总超时。线上实测：3/5 专家贴着 180s 整被时间砍断（工具调满、
    # 证据拿到 3 条仍差最后 finalize）——GLM 5 步推理 + 2 工具 ~200s 是常态。
    # 240s 留余量；议会总预算 1200s 仍足够（research 最慢 240 + 辩论 350 + 其他 200）。
    agent_total_timeout_seconds: int = 240
    agent_research_node_timeout_seconds: int = 135
    agent_max_observation_chars: int = 4000
    agent_max_evidence: int = 3
    expert_research_mode: str = "react_agent"

    # LangGraph checkpoint（本地联调可通过环境变量关闭，生产默认开启）
    langgraph_checkpointer_enabled: bool = True

    # ============================================================
    # Worker 进程防呆 + orphan 自愈 + 公平分槽
    # ============================================================
    # L1/L2 重复 worker 调试开关：true 时跳过 flock + DB heartbeat 启动闸
    # （只用于本机需要同时跑多个 worker 联调时）
    worker_allow_duplicate: bool = False

    # L3 orphan 自愈阈值（秒）。必须 > parliament_total_timeout，否则正常议会被误杀。
    # 议会最长 1200s，留 5 分钟 cushion，故 1800s。
    parliament_stale_seconds: int = 1800

    # 每议会公平分槽：单议会内同时飞的最大 LLM 请求数。
    # 配合全局 LLM_MAX_CONCURRENCY 实现"跨议会公平 + 议会内公平"。
    # 例：全局 5 + 每议会 2 → 两议会各占 2 槽，剩 1 槽给 summary/election 等单点调用。
    per_parliament_llm_concurrency: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
