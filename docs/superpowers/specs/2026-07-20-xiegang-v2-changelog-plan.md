# 邪杠 V2 改版方案（全修深挖 A 方案）

- 日期：2026-08-XX
- 状态：执行中
- 范围：解决 V1 遗留 6 项技术债，分 3 个 Step

## 六大改造点（已通过 Explore 子代理实码验证）

| # | 项 | 现状 | V2 目标 |
|---|---|---|---|
| 1 | 无 checkpointer | 每次 SSE 全图重跑，重启丢 | Postgres checkpointer + thread_id |
| 2 | runtime_context 是 module Dict | 重启全丢、多 worker 不共享 | Postgres-backed |
| 3 | `asyncio.to_thread(invoke)` | wrapper 不支持 ainvoke | `_StructuredWrapper.ainvoke()` 真异步 |
| 4 | cancel 是软取消（等节点完成） | 单次 LLM 调用 20-30s 才停 | ainvoke + asyncio.wait_for 硬中断 |
| 5 | memory_query 纯死码 + `{memories}` 永远空 | 跨议会记忆形同虚设 | 接 GLM embedding + pgvector |
| 6 | 5 议员工具都是 web_search 薄包装 | 无差异化数据源 | 接真实数据源 |

## 决策记录

- Checkpointer：**Postgres**（复用 Supabase 省运维）
- 取消机制：**ainvoke + asyncio.wait_for**（用户选）
- embedding: 先联调验证 GLM 智谱是否真支持 embedding-3，不通则降级到关键词匹配

## 三步走

| Step | 范围 | 时间 |
|---|---|---|
| **S1** | checkpointer + state cache DB 化 | 6-8h |
| **S2** | ainvoke 硬化 + cancel 真立即生效 | 5-7h |
| **S3** | embedding memory + 5 议员工具差异化 | 8-12h |

---

## S1：Checkpointer + State DB 化

### S1.1 装 langgraph-checkpoint-postgres
- [ ] `pip install langgraph-checkpoint-postgres`
- [ ] 验证 `from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver`
- [ ] 加到 pyproject.toml 依赖

### S1.2 加 parliament_runtime 表
- [ ] `scripts/migrate_004.py` 创建表 + 跑到 Supabase
```sql
CREATE TABLE IF NOT EXISTS parliament_runtime (
    parliament_id UUID PRIMARY KEY REFERENCES parliaments(id),
    user_interrupt TEXT,
    cancel_requested BOOLEAN DEFAULT FALSE,
    is_running BOOLEAN DEFAULT FALSE,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

### S1.3 get_checkpointer 单例
- [ ] 新建 `app/checkpointer.py`：AsyncPostgresSaver 单例 + setup()
- [ ] 复用 settings.supabase_db_url，去掉 `?pgbouncer=true`

### S1.4 graph/parliament.py 接受 checkpointer
- [ ] `build_parliament_graph(checkpointer=None)` 接受 checkpointer
- [ ] compile 时传入

### S1.5 stream.py 传 thread_id
- [ ] `astream(initial_state, config={"configurable": {"thread_id": parliament_id}})`
- [ ] 续盘逻辑：checkpoint 有该 thread → 续；无 → 全图跑

### S1.6 runtime_context 改读 DB（保留原接口签名）
- [ ] 所有 set/consume/cancel 函数 → 改为读写 Postgres
- [ ] 保留 `_clear_all_for_tests()` 用于测试

### S1.7 测试
- [ ] 改 runtime_context 测试为 mock DB
- [ ] 加 checkpoint 续盘测试（用 MemorySaver 测逻辑）

### S1 commit: `feat(V2-S1): Postgres checkpointer + runtime DB 化`

---

## S2：Ainvoke 硬化 + 硬中断 cancel

### S2.1 `_StructuredWrapper.ainvoke()`
- [ ] 给 llm.py 的 _StructuredWrapper 加 async ainvoke
- [ ] 走 structured.ainvoke() → fallback llm.ainvoke() + 同步 JSON pickup

### S2.2 parallel_research + round_debate 切 ainvoke
- [ ] `asyncio.to_thread(invoke, prompt)` → `await ainvoke(prompt)`
- [ ] 测试 mock AsyncMock

### S2.3 cancel 硬中断
- [ ] inflight task 注册表 module Dict[parliament_id → set[Task]]
- [ ] `asyncio.wait_for(task, timeout=120)` 包裹每次 ainvoke
- [ ] `request_cancel()` → 遍历 inflight task all.cancel()

### S2.4 cancel 信号从 stream.py 传到 node 内部
- [ ] LangGraph 节点函数检查 `is_cancelled()`
- [ ] 招 CancelledError 让 graph 停

### S2.5 测试
- [ ] AsyncMock 所有 LLM
- [ ] test_cancel_hard_interrupt: 慢 mock LLM + cancel + 验证 1s 内停

### S2 commit: `feat(V2-S2): ainvoke 真异步 + cancel 硬中断`

---

## S3：Embedding memory + 工具差异化

### S3.1 验证 GLM embedding
- [ ] 写 `scripts/test_embedding.py` 跑智谱 embedding-3
- [ ] 跑通 → 接入；不通 → 降级到关键词匹配

### S3.2 memory_query 真 embedding 检索
- [ ] query 向量化 → pgvector 余弦相似 → LIMIT 3
- [ ] archive.py 计算 topic_embedding 入库

### S3.3 议员 prompt 真填 `{memories}`
- [ ] parallel_research 调 memory_query
- [ ] 注入到 prompt 第 5 层

### S3.4 5 议员工具差异化
- [ ] tools/topic_specific.py：
  - historical_risk_lookup: 加 wikipedia / zhihu 关键词
  - market_window_check: 加 macro 数据 (S3 PoC)
  - trend_research: 保 web_search
  - contrarian_search: 加 reddit / 知乎
  - data_table: calculator 表格模板

### S3.5 测试
- [ ] test_memory_query (mock embedding)
- [ ] test_topic_specific

### S3 commit: `feat(V2-S3): embedding memory + 工具差异化`

---

## 实施记录

| Step | commit | 日期 | 测试数 | 备注 |
|---|---|---|---|---|
| S1 | （待填） |  |  | checkpointer + state DB |
| S2 | （待填） |  |  | ainvoke + cancel 硬 |
| S3 | （待填） |  |  | embedding + 工具 |

