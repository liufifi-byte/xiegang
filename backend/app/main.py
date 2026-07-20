"""组装 FastAPI app：所有路由 + CORS。"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.parliament import router as parliament_router
from app.api.stream import router as stream_router
from app.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="邪杠 Backend",
    version="0.1.0",
    description="Multi-agent decision parliament — 让 5 个 AI 议员为你的决策吵架",
)

# P0-S1.4: CORS 从 env 读取
# .env 里 CORS_ORIGINS="http://localhost:3000,https://xiegang.vercel.app"
# 缺省 fallback 到本地
_cors_list = [
    o.strip()
    for o in settings.cors_origins.split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Device-Id"],  # P0-S1.1: 让前端能拿到回传 device_id
)

app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(parliament_router, tags=["parliament"])
app.include_router(stream_router, tags=["stream"])


@app.get("/")
async def root() -> dict:
    return {"message": "邪杠 backend is running", "env": settings.app_env}


@app.get("/api")
async def api_root() -> dict:
    return {
        "endpoints": [
            "GET  /api/health",
            "POST /api/parliament            — 创建议会",
            "GET  /api/parliament/{{id}}          — 查议会状态",
            "GET  /api/parliament/{{id}}/stream   — SSE 流",
        ]
    }
