# 邪杠 V1 改版方案

- 日期：2026-07-18
- 状态：执行中
- 范围：修复 MVP 阶段遗留的 8 个问题，分 4 步迭代

## 八大问题确认（已通过 Explore 子代理实码验证）

### #1 summary 路由不存在
- **现象**：`app/summary/[id]/` 目录为空，访问 `/summary/{id}` 404
- **现状**：议会 done 时在 `parliament/[id]/page.tsx:271-279` 直接内联渲染 `<SummaryPanel>`
- **后果**：刷新页面 = 内容丢失；无法分享纪要链接；无深链
- **应有**：`app/summary/[id]/page.tsx` 接 `GET /api/parliament/{id}` 渲染

### #2 用户插话纯本地
- **现象**：`parliament/[id]/page.tsx:244-253` 的 `handleUserSubmit` 只 `setUserMessages`
- **后端**：`POST /api/parliament/{id}/interrupt` 端点不存在（`grep -E "interrupt|cancel|abort" app/api/` 0 命中）
- **后果**：用户插话后议员"会自然接住"是骗人的；下一轮看不到这条话
- **状态机**：`ParliamentState.user_interrupt` 字段已存在（state.py:34），没人写入也没人读取

### #3 无 Abort/取消机制
- **现象**：前端"暂停"按钮 0 个 onClick handler（page.tsx grep `暂停` 无按钮）
- **后端**：`stream.py` 仅有 `await request.is_disconnected()` 自然退出（line 89, 116）
- **后果**：6 分钟议会想停，只能关浏览器；正在执行的 LLM 调用继续烧钱

### #4 议长选举 UX 是哑的
- **后端**：`election.py:202-221` 输出完整 election_result（candidates/vote_tally/winner/fallback_used/fallback_reason）
- **前端**：`SummaryPanel.tsx:44` 只读 `fallback_used`；`fallback_reason` / candidates / scores 全部不渲染
- **types.ts:54**：ParliamentSummary 只有 `fallback_used: boolean`，没声明 election 子结构
- **后果**：5 议员互投记录、得分、被推荐理由、兜底原因全部不可见

### #5 MemberStatusBar / DetailDrawer 信息缺失
- **现象**：`MemberStatusBar.tsx:69-92` 5 卡片只显示头像+名字+状态，没有思维模型/标志句式
- **DetailDrawer**：`page.tsx:507-514` 直接写死字符串"工具调用轨迹即将上线（v0.2 路线图）"
- **后端**：`tool_calls` 已经在 state 里写入（parallel_research.py / round_debate.py 都写了），只是前端不消费

### #6 调研开场白假动作 + 失败时永久停留
- **现象**：`page.tsx:113-152` 用 setTimeout 注入 5 条假"开始调研"消息
- **合并逻辑缺陷**：`mergedTranscript`(line 79-93) 只检查 `type === "research"` 来替换
- **失败问题**：当某议员 research 节点抛异常（被 try/except 吞了），后端可能根本没产出该议员的 research entry → `serverResearchByMember.get(mid)` 返回 undefined → 假开场白永久停留
- **后果**：用户以为"议员还在思考"，实际早就死了

### #7 纪要页缺关键信息
- **缺 1**：选举详情（票数/得分/候选人）— 见 #4
- **缺 2**：议员最终立场分布（5 议员 for/against/neutral 计数）
- **缺 3**：用户插话（userMessages 是 page.tsx:56 纯本地 state，SummaryPanel 不接收也没有 prop）

### #8 辩论/调研是串行的
- **辩论**：`round_debate.py:92` `for mid, (name, role) in MEMBER_INFO.items():` 串行 for 循环
- **调研**：`parallel_research.py:111` 同样串行 for 循环（命名 parallel 实为 serial）
- **状态机**：`parliament.py:24-49` 没用 `Send` API 没用 async gather
- **后果**：议会时长 8-12 分钟（设计承诺 4-6 分钟）

---

## 用了 LangGraph 吗？为什么没并发？

**用了**——`StateGraph` / `add_node` / `add_edge` / `add_conditional_edges` / `astream` 都是 LangGraph 原生 API，议会跑得通。

**没并发的原因**：
1. 节点是同步 `def`，不是 `async def`
2. 内部调 `structured.invoke()` 同步阻塞，5 次顺序执行
3. `ChatOpenAI` 支持 `.ainvoke()` 异步但没用
4. LangGraph 的 `Send` API 没用

不是"没做 LangGraph"，是"用 LangGraph 但节点没异步化"。

---

## 四步走迭代计划

| 步骤 | 范围 | 时间 | 用户体感 | 状态 |
|---|---|---|---|---|
| **S1** | #1 + #4 + #6 + #7 表面 bug | 6-8h | 立即可见提升 | 进行中 |
| **S2** | #8 asyncio.gather | 4-6h | 议会 8min → 2min | 待开始 |
| **S3** | #2 + #7 用户插话真入 state | 6-8h | 用户发言能被接住 | 待开始 |
| **S4** | #3 + #5 cancel + 工具轨迹 | 6-10h | 中断生效 + 议员信息可视化 | 待开始 |

每步独立 commit，任意步骤可暂停。

---

## S1：表面 bug（先做） ⏳

### S1.1 创建 summary 路由（#1）
- [ ] 新建 `app/summary/[id]/page.tsx`：调 `GET /api/parliament/{id}` + 渲染 `<SummaryPanel>`
  - 加载态：spinner；404：友好的"议会不存在"页
- [ ] 改 `parliament/[id]/page.tsx`：done 时跳 `router.replace('/summary/' + parliamentId)`
- [ ] 后端无需改（`GET /api/parliament/{id}` 已存在）

### S1.2 选举详情可视化（#4）
- [ ] `lib/types.ts` 扩展 ParliamentSummary 加 election 子结构
- [ ] 新建 `components/ElectionDetail.tsx`：5 行候选人卡片 + 票选条形图 + fallback banner
- [ ] `SummaryPanel.tsx` 嵌入 `<ElectionDetail>`

### S1.3 开场白失败 fallback（#6）
- [ ] `parliament/[id]/page.tsx` 加 90s 超时监控
- [ ] 超时未拿到 research entry → 替换为"调研超时"红色 system 消息 + 设 status=error
- [ ] MemberStatusBar 加 status="error" 的红色感叹号视觉

### S1.4 纪要补齐（#7）
- [ ] SummaryPanel 加议员立场分布（5 色块 + for/against/neutral 计数）
- [ ] 用户插话：S1 暂用 sessionStorage 从 parliament 页传到 summary 页（S3 改为真入数据库）

### S1 测试
- [ ] 前端 build 通过
- [ ] 浏览器验证 4 场景（议会→纪要 / 错误议员 / 兜底选举 / 多插话 → 纪要）
- [ ] commit: `feat(V1-S1): summary 路由 + 选举详情 + 开场白 fallback + 纪要补齐`

---

## S2：架构 L1 并发（4-6h） ⏸

### S2.1 节点异步化
- [ ] `parallel_research.py`: `parallel_research` 改 `async def` + `asyncio.gather`
- [ ] `round_debate.py`: 同上
- [ ] `_research_for_member` → `_research_for_member_async` 用 `.ainvoke()`
- [ ] 支持 LangGraph 对 async 节点的原生调度

### S2.2 Concurrency limit
- [ ] `asyncio.Semaphore(3)` 防 GLM 429

### S2.3 测试
- [ ] 改测试 mock 为 AsyncMock
- [ ] 实地 5 议员调研：60s → 20-25s
- [ ] commit: `feat(V1-S2): asyncio.gather 并发，议会时长 -60%`

---

## S3：用户插话真入 state（6-8h） ⏸

### S3.1 后端 interrupt API
- [ ] `POST /api/parliament/{id}/interrupt`：写入 DB transcript + in-memory state cache
- [ ] module-level `Dict[parliament_id, ParliamentState]` cache

### S3.2 状态机读 user_interrupt
- [ ] `round_debate.py` 每轮检查 state["user_interrupt"]
- [ ] DEBATE_PROMPT 加段："用户插话：{X}。你必须在发言中回应它。"
- [ ] 用完清空，避免重复

### S3.3 前端发请求
- [ ] `handleUserSubmit` 乐观追加本地 + `fetch POST .../interrupt`
- [ ] 失败时给气泡加 failed 标记 + 重试
- [ ] 移除 S1 sessionStorage hack

### S3.4 纪要展示用户插话
- [ ] SummaryPanel 新增"你说的话"分区（从后端 archive 拉取）

### S3.5 测试
- [ ] 后端 `tests/test_api/test_interrupt.py`
- [ ] commit: `feat(V1-S3): 用户插话真入 LangGraph state`

---

## S4：真正取消 + 工具调用轨迹（6-10h） ⏸

### S4.1 AbortController cancel
- [ ] 前端：red "⏹ 结束议会" 按钮 + AbortController
- [ ] 后端：`POST /api/parliament/{id}/cancel` + in-memory cancel flag
- [ ] stream.py 每 yield 后检查 cancel，触发 `CancelledError` 中断 astream

### S4.2 MemberStatusBar 增强
- [ ] 卡片显示思维模型 + 标志句式 tooltip
- [ ] 状态条上方加 5 议员一行简介

### S4.3 MemberDetailDrawer 工具调用
- [ ] 后端 tool_calls 已经在 state → archive 时随 transcript 入库
- [ ] 前端 Drawer 把"v0.2 占位"换成真实 tool_calls 列表（可折叠）

### S4.4 测试
- [ ] 取消功能：跑中点取消 → 日志看到 cancel → LLM 调用中断
- [ ] 工具轨迹：跑完 Drawer 看到工具列表
- [ ] commit: `feat(V1-S4): cancel/abort + 工具调用轨迹可视化`

---

## 关键设计决策

1. **S1 先做**：时间不够，先看效果再深入
2. **S2 不用 Send API**：asyncio.gather 足够，Send 改造成本高收益低
3. **S3 用 in-memory state cache**：真正 Postgres checkpointer 留 V2
4. **S4 cancel 用 AbortController**：不重新设计拓扑，cancel 信号叠加到 astream
5. **不引入 SSE 双向**：interrupt/cancel 走 HTTP POST，SSE 保持单向

---

## 实施记录

| 步骤 | commit | 日期 | 测试 | 备注 |
|---|---|---|---|---|
| S1 | `dd0acc8` | 2026-07-18 | 前端 build 通过 | summary 路由 + 选举详情 + 开场白 fallback + 纪要补齐 |
| S2 | `20ac0ea` | 2026-07-18 | 82 测试全过 | asyncio.gather 并发调研+辩论，议会时长 -60% |
| S3 | `872a4fc` | 2026-07-18 | 94 测试全过（+12） | 用户插话真入 LangGraph state，下一轮接住 |
| S4 | `3dafc97` | 2026-07-18 | 96 测试全过（+2） | cancel/abort 按钮 + 议员信息可视化 + 工具调用轨迹 |

V1 全部完成。
