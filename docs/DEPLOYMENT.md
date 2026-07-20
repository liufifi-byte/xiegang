# 邪杠会议室 部署文档

## P0-S2 双服务架构

```
                    Supabase Postgres (pgvector)
                          ↑       ↑
                          │       │
       ┌──────────────────┤       │
       │                          │
Railway service: web        Railway service: worker
  uvicorn app.main:app        python -m app.worker
  (FastAPI + SSE 订阅器)       (跑 LangGraph 6 节点)
       ↑
       │
   Vercel: 前端
   Next.js 15
```

## Railway 配置

### Service 1: web

- **Source**: 你的 github 仓库
- **Root Directory**: (整个 repo)
- **Build Command**: `cd backend && pip install -e ".[dev]"`
- **Start Command**: `cd backend && uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- **Healthcheck**: `GET /api/health` → 200
- **Environment Variables**: 见 `.env.example`

### Service 2: worker

- **Source**: 同一仓库
- **Root Directory**: (整个 repo)
- **Build Command**: `cd backend && pip install -e ".[dev]"`
- **Start Command**: `cd backend && python -m app.worker`
- **Environment Variables**: 跟 web 完全一致
- **不需要 Healthcheck**（无 HTTP 端口）
- 建议启用 Railway 的"restart if exits with non-zero"

### 横向扩展

- 想跑更多议会同时 → 多启几个 worker 实例（SKIP LOCKED 保证不抢同一行）
- 单实例默认 `_MAX_CONCURRENT = 2`，可在 `worker.py` 调
- web service 通常 1 个够（SSE 是单向）

## Vercel 配置（前端）

- **Framework Preset**: Next.js
- **Root Directory**: `frontend`
- **Build Command**: `npm run build`
- **Environment Variables**:
  - `NEXT_PUBLIC_API_URL=https://<web-service>.up.railway.app`

## Supabase 配置

跑全部 migration 脚本（按编号顺序）：

```bash
cd backend
./.venv/bin/python scripts/migrate_007.py
# 之前的 002/003/004_retry/005_fix/006_fix 也要跑（首次部署）
```

## 本地开发（可选：起 worker）

```bash
# Terminal 1: web
cd backend
./.venv/bin/uvicorn app.main:app --reload --port 8001 --host 127.0.0.1

# Terminal 2: worker
cd backend
./.venv/bin/python -m app.worker

# Terminal 3: frontend
cd frontend
npm run dev
```

## 监控

`web` 日志：FastAPI 标准 + 限流 429 warning
`worker` 日志：每个议会 started/done/failed/cancelled

P0-S1.4 的 `app/monitoring.py` 提供 structured log 接口，V3 接 Sentry 时换 sink。
