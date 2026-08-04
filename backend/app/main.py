"""组装 FastAPI app：所有路由 + CORS。"""
import fcntl
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.expert_selection import router as expert_selection_router
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
app.include_router(expert_selection_router)
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


# ============================================================
# API 进程单例锁：防止两个 uvicorn 进程都来跑这个模块（同机器时常见 tap）
# uvicorn --reload 模式下 reload watcher 会 import 这个模块两次，所以只锁 real worker
# process（看 sys.argv 是否含 "--reload" 来分辨）。--reload 模式下跳过锁。
# ============================================================
def _acquire_api_lock_or_exit() -> None:
    """同机器只允许一个 uvicorn worker 跑 api。失败时 sys.exit 非 0。"""
    if settings.worker_allow_duplicate:  # 复用同一个开关
        return
    # uvicorn --reload 会有 reloader 子进程也 import 本模块，跳过
    if "--reload" in sys.argv or any("reload" in a for a in sys.argv):
        return

    lock_path = Path(os.environ.get(
        "API_LOCK_PATH",
        str(Path(__file__).resolve().parent.parent / ".api.lock"),
    ))
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, f"api pid={os.getpid()}\n".encode())
        # fd 故意保持打开不 close——进程退出时 OS 自动释放 flock
        globals()["_api_lock_fd"] = fd
    except (BlockingIOError, OSError) as exc:
        logging.error(
            "已有 API 进程在跑（.api.lock 抢占失败: %s）。"
            "如确定要起，请先 kill 旧 uvicorn 或删除 %s。"
            "调试可设 WORKER_ALLOW_DUPLICATE=true。",
            exc, lock_path,
        )
        raise SystemExit(3)


_acquire_api_lock_or_exit()
