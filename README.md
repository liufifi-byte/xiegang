# 邪杠会议室

> 先杠一杠，再下结论 — 5 个有不同思维模型的 AI 议员为你的真实决策吵架。

## 项目结构

```
xiegang/
├── backend/          # FastAPI + LangGraph + GLM-5
│   ├── app/
│   ├── scripts/      # migrate_001 ~ 006, test_embedding, seed_members
│   └── tests/        # 122 个测试
├── frontend/         # Next.js 15 + Tailwind 贤者厅配色
│   ├── app/
│   ├── components/
│   └── lib/
└── docs/             # 设计文档 + V1/V2 改版方案
```

## 本地启动

### 1. 配置

根目录 `.env`：

```
GLM_API_KEY=...           # 智谱开放平台
GLM_MODEL=glm-5
GLM_EMBEDDING_MODEL=embedding-3
SUPABASE_URL=...
SUPABASE_KEY=...
SUPABASE_DB_URL=...
TAVILY_API_KEY=...
```

### 2. 启动后端

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8001 --host 127.0.0.1
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev      # 默认 3001 端口
```

访问 http://127.0.0.1:3001

### 4. 首次部署数据库

```bash
# 在 backend 目录下，按顺序执行迁移
./.venv/bin/python scripts/seed_members.py
./.venv/bin/python scripts/migrate_002.py
./.venv/bin/python scripts/migrate_003.py
./.venv/bin/python scripts/migrate_004_retry.py
./.venv/bin/python scripts/migrate_005_fix.py
./.venv/bin/python scripts/migrate_006_fix.py
```

## 测试

```bash
cd backend && source .venv/bin/activate && pytest tests/ -v
```

## 部署

- 前端：Vercel → `NEXT_PUBLIC_API_URL=https://your-backend.railway.app`
- 后端：Railway
- 数据库：Supabase（Postgres + pgvector）

## 文档

- `docs/superpowers/specs/2026-07-17-council-design.md` — 设计文档
- `docs/superpowers/specs/2026-07-18-xiegang-v1-changelog-plan.md` — V1 改版 4 步
- `docs/superpowers/specs/2026-07-20-xiegang-v2-changelog-plan.md` — V2 改版 3 步

## 项目演进

### V1（4 步，commit dd0acc8 / 20ac0ea / 872a4fc / 3dafc97）
- S1: summary 路由 + 选举详情 + 开场白 fallback + 纪要补齐
- S2: asyncio.gather 并发调研+辩论
- S3: 用户插话真入 runtime_context
- S4: cancel/abort 按钮 + 议员信息可视化 + 工具调用轨迹

### V2（3 步，commit c21c50f / 1be8ad2 / 5e1a0cc）
- S1: Postgres checkpointer + runtime DB 化
- S2: _StructuredWrapper.ainvoke() 真异步 + cancel_guard 硬中断
- S3: GLM embedding-3 + pgvector 跨议会记忆 + 5 工具差异化
