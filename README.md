# 邪杠 Council

5 个 AI 议员为你的真实决策展开一场思想辩论。

## 本地开发

### 1. 启动本地 Postgres

```bash
docker compose up -d postgres
```

### 2. 后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env  # 填实际值（GLM_API_KEY 等）
psql "postgresql://council:council@localhost:5433/council" -f migrations/001_initial.sql
uvicorn app.main:app --reload --port 8000
```

### 3. 前端

```bash
cd frontend
pnpm install
pnpm dev
```

访问 http://localhost:3000

## 部署

- 前端：Vercel
- 后端：Railway
- 数据库：Supabase

## 文档

- 设计文档：`../docs/superpowers/specs/2026-07-17-council-design.md`
- 实现计划：`../docs/superpowers/plans/2026-07-17-council-implementation.md`
- 交接文档：`../docs/superpowers/handoff/council-handoff.md`
