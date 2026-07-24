# 斜杠二级会议体验改版实施计划

日期：2026-07-24

依据：[2026-07-24-council-room-experience-redesign.md](./2026-07-24-council-room-experience-redesign.md)

目标：实现“朋友版外壳 + 真实 Agent 议会”，加入可调整的默认专家选择、稳定产品事件、可恢复会议状态、收敛后的议会工作流，以及完整的排队和异常反馈。

## 实施原则

- 按后端契约、前端选择页、工作流、事件恢复、会议 UI、异常交互、发布验证的顺序推进。
- 每个阶段独立提交并通过对应测试，避免一次性重写。
- 保留现有职业专家 Agent、Harness、公开安全轨迹和历史会议兼容逻辑。
- 不提交或覆盖当前首页未提交改动，接入首页时只修改必要重叠代码并人工核对。
- 一期不支持自定义讨论者。
- 前端不依赖 LangGraph 内部节点名，统一消费稳定产品事件。

## 阶段 0：建立基线与保护现有改动

### 目标

确认现有测试、构建和工作区状态，建立改版前基线。

### 操作

1. 记录 `git status --short`。
2. 确认并保护以下现有首页改动：
   - `frontend/app/page.tsx`
   - `frontend/components/HomeSceneTabs.tsx`
   - `frontend/public/brand/xiegang-logo.png`
3. 运行后端全量测试。
4. 运行前端生产构建。
5. 记录当前真实会议耗时、完成状态和事件数量，作为改版对照。

### 验证

```bash
cd backend && ./.venv/bin/pytest tests/ -q
cd frontend && ./node_modules/.bin/next build
```

### 提交

无代码提交；将基线结果记录到最终验证文档。

## 阶段 1：专家推荐预览与最终选择契约

### 目标

首页提交后先获得默认五位专家，不创建正式会议；创建会议时后端严格采用用户最终选择的 3–5 位。

### 后端文件

- 新建 `backend/app/services/expert_selection.py`
- 新建 `backend/app/schemas/expert_selection.py`
- 新建 `backend/app/api/expert_selection.py`
- 修改 `backend/app/main.py`
- 修改 `backend/app/schemas/parliament.py`
- 修改 `backend/app/api/parliament.py`
- 修改 `backend/app/graph/state.py`
- 修改 `backend/app/graph/nodes/select_experts.py`
- 修改 `backend/app/worker.py`

### 具体任务

1. 抽取现有专家选择逻辑为纯服务函数。
2. 为八个固定场景建立确定性推荐映射。
3. 通用场景复用轻量主题分类；失败时返回稳定默认五人。
4. 新增 `POST /api/expert-selection/preview`。
5. 响应包含：
   - `recommended_expert_ids`
   - `coverage`
   - `reasons`
   - `selection_source`
6. 创建会议请求新增 `selected_expert_ids`。
7. 验证专家 ID 合法、无重复、数量为 3–5。
8. 将最终选择持久化到 `parliaments.selected_expert_ids`。
9. `select_experts` 节点检测到用户选择时只做状态初始化，不重新覆盖。
10. 保留旧会议无该字段时的自动选择兼容路径。

### 测试

- 新建 `backend/tests/test_api/test_expert_selection.py`
- 扩展 `backend/tests/test_api/test_parliament_api.py`
- 扩展 `backend/tests/test_nodes/test_select_experts.py`
- 扩展 `backend/tests/test_state.py`

覆盖：

- 固定场景不调用模型。
- 通用场景正常推荐。
- 推荐失败回退。
- 少于 3 位、多于 5 位、重复或未知 ID 返回 422。
- 最终选择进入状态且不被覆盖。
- 旧请求仍能工作。

### 提交

`feat: add expert selection preview and final roster`

## 阶段 2：专家选择页与首页接入

### 目标

实现与朋友版一致的“选择讨论者”页面，默认勾选五位并允许用户调整。

### 前端文件

- 新建 `frontend/app/council/setup/page.tsx`
- 新建 `frontend/components/council/ExpertSelectionCard.tsx`
- 新建 `frontend/components/council/ExpertSelectionGrid.tsx`
- 新建 `frontend/components/council/SetupTopicPanel.tsx`
- 新建 `frontend/lib/council-draft.ts`
- 修改 `frontend/lib/api.ts`
- 修改 `frontend/lib/types.ts`
- 修改 `frontend/app/page.tsx`
- 修改 `frontend/components/HomeSceneTabs.tsx`
- 修改 `frontend/app/globals.css`

### 具体任务

1. 首页提交后将议题、场景和背景保存为短生命周期草稿。
2. 跳转 `/council/setup`，不直接创建会议。
3. 选择页请求推荐预览并默认勾选五位。
4. 推荐加载时先显示六张稳定骨架卡片。
5. 推荐失败时使用默认五人并显示非阻塞提示。
6. 专家卡展示职业、方法、针对当前议题的推荐理由和选择状态。
7. 限制选择 3–5 位：
   - 选择达到 5 位后，未选卡仍可点击，但先提示替换或取消一位。
   - 低于 3 位时禁用开始按钮并解释原因。
8. 点击开始后创建一次会议，防止重复提交。
9. 创建失败时保留全部草稿与选择，允许原地重试。
10. 桌面端使用朋友版左右布局，移动端改为单列。

### 验证

- 固定场景和通用场景均可进入选择页。
- 默认五人正确。
- 用户可以取消并选择第六位。
- 刷新选择页草稿仍在。
- 创建成功后只产生一场会议。
- 390px 宽度无横向溢出。

### 提交

`feat: add recommended expert setup flow`

## 阶段 3：收敛议会工作流与取消行为

### 目标

将三轮全员辩论收敛为五位调研、三位差异交锋、主席总结，并确保取消和超时能在合理边界生效。

### 后端文件

- 新建 `backend/app/graph/nodes/select_divergent_experts.py`
- 新建 `backend/app/graph/nodes/cross_examination.py`
- 新建 `backend/app/graph/nodes/chair_summary.py`
- 修改 `backend/app/graph/parliament.py`
- 修改 `backend/app/graph/state.py`
- 修改 `backend/app/graph/nodes/parallel_research.py`
- 修改 `backend/app/cancel_guard.py`
- 修改 `backend/app/runtime_context.py`
- 修改 `backend/app/worker.py`
- 修改 `backend/app/config.py`
- 保留但停止在新图中调用 `backend/app/graph/nodes/round_debate.py`
- 保留旧归档兼容所需的 `election.py` 和旧类型

### 具体任务

1. 固定新图：
   - 理解问题
   - 专家并行调研
   - 选择差异最大的三位
   - 一次交叉回应
   - 主席总结
   - 归档
2. 差异评分使用可测试的确定性特征：
   - 立场方向。
   - 关键变量。
   - 方法类别。
   - 置信度。
   - 是否降级。
3. 不使用额外模型调用完成差异选择。
4. 交叉回应只调用三位专家，每位一次。
5. 主席选择采用确定性质量评分，不再运行五人自荐和互投。
6. 每个阶段开始和结束检查取消。
7. 每个专家调用和工具调用都绑定议会取消令牌。
8. 设置阶段超时和整场硬超时。
9. 取消或硬超时后使用已有结果进入部分总结，而不是只标失败。
10. Worker 收到取消后不再等待完整辩论节点结束。

### 测试

- 新建 `backend/tests/test_nodes/test_select_divergent_experts.py`
- 新建 `backend/tests/test_nodes/test_cross_examination.py`
- 新建 `backend/tests/test_nodes/test_chair_summary.py`
- 修改 `backend/tests/test_graph.py`
- 扩展 `backend/tests/test_cancel_guard.py`
- 扩展 `backend/tests/test_api/test_cancel.py`

覆盖：

- 恰好选择三位差异专家。
- 降级结果不会无条件胜出。
- 交叉回应最多三次模型调用。
- 单人失败仍进入总结。
- 调研、交锋和总结阶段均能取消。
- 超时生成部分结果。
- 新图不再进入旧三轮辩论。

### 提交

`feat: streamline council debate and partial completion`

## 阶段 4：稳定产品事件与可恢复快照

### 目标

让前端只消费稳定产品状态，页面刷新或断线后可以完整恢复。

### 后端文件

- 新建 `backend/app/council_events.py`
- 新建 `backend/app/council_snapshot.py`
- 修改 `backend/app/event_store.py`
- 修改 `backend/app/schemas/event.py`
- 修改 `backend/app/schemas/parliament.py`
- 修改 `backend/app/api/parliament.py`
- 修改 `backend/app/api/stream.py`
- 修改 `backend/app/worker.py`
- 修改各新工作流节点的事件发布

### 具体任务

1. 定义产品事件：
   - `council_queued`
   - `council_started`
   - `phase_changed`
   - `expert_started`
   - `expert_action`
   - `expert_opinion`
   - `expert_failed`
   - `user_context_applied`
   - `chair_summary_started`
   - `council_completed`
   - `council_partial`
   - `council_cancelled`
2. 所有事件带单调递增 `seq`、会议 ID、时间和公开 payload。
3. 将 LangGraph 内部事件映射为产品事件，前端不读取节点名。
4. 快照聚合：
   - 当前状态和阶段。
   - 最终专家名单。
   - 专家状态。
   - 时间线。
   - 公开 Agent 行动。
   - 用户补充应用状态。
   - 部分结果状态。
5. `GET /api/parliament/{id}` 返回完整快照。
6. SSE 支持 `after_seq` 或 `Last-Event-ID` 续订。
7. 重连时先补历史事件，再推送新事件。
8. 对重复事件保持幂等。

### 测试

- 新建 `backend/tests/test_council_events.py`
- 新建 `backend/tests/test_council_snapshot.py`
- 扩展 `backend/tests/test_event_store.py`
- 扩展 `backend/tests/test_api/test_parliament_api.py`

覆盖：

- 事件序号严格递增。
- 内部节点名不暴露给前端协议。
- 快照可重建专家状态和阶段。
- `after_seq` 不重复、不漏事件。
- 部分成功、取消和失败均有可恢复终态。

### 提交

`feat: add recoverable council product events`

## 阶段 5：重构会议页为朋友版四栏布局

### 目标

重构现有单文件会议页，落地 C 方案桌面端和移动端 UI。

### 前端文件

- 重写 `frontend/app/parliament/[id]/page.tsx`
- 新建 `frontend/components/council/CouncilShell.tsx`
- 新建 `frontend/components/council/CouncilRail.tsx`
- 新建 `frontend/components/council/ConversationSidebar.tsx`
- 新建 `frontend/components/council/CouncilHeader.tsx`
- 新建 `frontend/components/council/CouncilPhaseBar.tsx`
- 新建 `frontend/components/council/ExpertSeatPanel.tsx`
- 新建 `frontend/components/council/MobileExpertStrip.tsx`
- 新建 `frontend/components/council/CouncilTimeline.tsx`
- 新建 `frontend/components/council/CouncilMessage.tsx`
- 新建 `frontend/components/council/AgentActionSummary.tsx`
- 新建 `frontend/components/council/AgentActionDetail.tsx`
- 新建 `frontend/components/council/CouncilComposer.tsx`
- 新建 `frontend/lib/council-state.ts`
- 修改 `frontend/lib/sse.ts`
- 修改 `frontend/lib/types.ts`
- 修改 `frontend/app/globals.css`

### 具体任务

1. 页面首次加载先拉取快照，再订阅最后 `seq` 之后的事件。
2. 使用 reducer 统一处理快照和实时事件。
3. 桌面端四栏：
   - 窄导航栏。
   - 会话信息栏。
   - 聊天主区。
   - 专家席。
4. 专家观点保持时间线主要视觉权重。
5. Agent 行动显示为短摘要，默认折叠详情。
6. 显示四阶段进度，不展示 LangGraph 节点名。
7. 会议刚创建时立即显示选中专家。
8. 将“编辑参与者”改为“查看专家”。
9. 保留旧归档会议角色兼容。
10. 移动端隐藏左右辅助栏，显示顶部专家横条。

### 验证

- 桌面端与朋友版四栏结构一致。
- 首屏不会出现空白专家席。
- 事件乱序或重复不会生成重复消息。
- 刷新后阶段、消息和专家状态一致。
- 390px、768px、1440px 三种视口无布局问题。
- Agent 详情不显示内部思维链。

### 提交

`feat: redesign council room around live experts`

## 阶段 6：用户补充、提前总结与完整异常状态

### 目标

让用户能够可靠参与讨论，并让排队、断线、失败、取消和提前总结均有明确反馈。

### 后端文件

- 修改 `backend/app/api/parliament.py`
- 修改 `backend/app/runtime_context.py`
- 修改 `backend/app/worker.py`
- 修改 `backend/app/graph/state.py`
- 修改 `backend/app/graph/nodes/cross_examination.py`
- 修改 `backend/app/graph/nodes/chair_summary.py`

### 前端文件

- 修改 `frontend/components/council/CouncilComposer.tsx`
- 新建 `frontend/components/council/CouncilStatusBanner.tsx`
- 新建 `frontend/components/council/CancelCouncilDialog.tsx`
- 修改 `frontend/components/council/CouncilTimeline.tsx`
- 修改 `frontend/lib/api.ts`
- 修改 `frontend/lib/council-state.ts`

### 具体任务

1. 用户消息使用客户端 ID，支持乐观展示和服务端确认。
2. 后端记录补充内容将影响的后续阶段。
3. 发布 `user_context_applied`；失败时前端标记“未应用”并允许重试。
4. 新增提前总结接口：
   - 至少三位专家有可用观点。
   - 停止开启新调用。
   - 进入主席总结。
   - 标记未完成专家。
5. 排队状态显示已等待时长。
6. SSE 断开显示恢复提示，指数退避重连。
7. 单专家失败显示“信息有限”，不阻断会议。
8. 叫停弹出确认框；确认后显示“正在停止”而不是立即伪装终止。
9. 超时、取消和部分成功均可进入部分纪要。
10. 创建、插话和总结按钮都有防重复提交。

### 测试

- 扩展 `backend/tests/test_api/test_interrupt.py`
- 扩展 `backend/tests/test_api/test_cancel.py`
- 新建 `backend/tests/test_api/test_early_summary.py`
- 前端通过 reducer 单元测试或最小浏览器场景覆盖：
  - 乐观消息确认。
  - 消息未应用和重试。
  - 提前总结按钮门槛。
  - 断线重连提示。
  - 取消中间态。

### 提交

`feat: add interactive and resilient council controls`

## 阶段 7：纪要页承接与部分结果展示

### 目标

让正常、提前、部分和取消会议都能得到清晰可用的结果。

### 文件

- 修改 `frontend/app/summary/[id]/page.tsx`
- 修改 `frontend/components/SummaryPanel.tsx`
- 修改 `frontend/components/ElectionDetail.tsx`
- 修改 `frontend/lib/types.ts`
- 修改 `backend/app/graph/nodes/archive.py`
- 修改 `backend/app/schemas/parliament.py`

### 具体任务

1. 纪要页沿用米白与梅紫视觉。
2. 展示：
   - 结论。
   - 共识与分歧。
   - 条件反转点。
   - 风险边界。
   - 下一步行动。
   - 证据。
3. 提前总结显示显著但不惊扰的状态说明。
4. 部分结果列出已完成和未完成专家。
5. 旧“议长选举”区域对新会议改为“主席综合依据”；旧会议仍按历史数据渲染。
6. 支持从纪要基于原问题重新开会。

### 验证

- 正常、提前、部分、取消四种结果可打开。
- 刷新不丢数据。
- 旧会议纪要仍可读。
- 移动端无溢出。

### 提交

`feat: align council summaries with partial outcomes`

## 阶段 8：全量验证与发布门槛

### 自动化验证

```bash
cd backend
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check app tests

cd ../frontend
./node_modules/.bin/next build
```

### 本地真实链路

至少验证：

1. 精选买房场景。
2. Offer 场景。
3. 通用关系决策。
4. 用户更换默认专家。
5. 调研阶段补充背景。
6. 三位专家完成后提前总结。
7. 单专家工具失败。
8. 实时连接断开并恢复。
9. 用户取消。
10. 页面刷新恢复。

### 20 案例测试

案例覆盖：

- 职业 4 例。
- 房产与消费 4 例。
- 投资与创业 4 例。
- 关系与家庭 4 例。
- 通用开放问题 4 例。

记录：

- 是否完成。
- 总耗时。
- 首条可见反馈耗时。
- 模型调用次数。
- 工具调用次数。
- 单场估算成本。
- 是否发生降级。
- 是否存在隐私或重复事件问题。

发布门槛：

- 完成率 ≥ 95%。
- P95 总耗时 ≤ 180 秒。
- 首屏专家与阶段反馈 ≤ 1 秒。
- 无隐私泄露。
- 无重复或缺失事件。
- 单专家失败仍产生可用纪要。
- 前端生产构建和后端全量测试通过。

### 验证文档

新增：

`docs/superpowers/specs/2026-07-24-council-room-launch-validation.md`

### 提交

`test: validate redesigned council experience`

## 推荐执行批次

为降低风险，按以下四批推进：

1. **选择闭环**：阶段 1–2。
2. **运行内核**：阶段 3–4。
3. **产品界面**：阶段 5–7。
4. **发布验证**：阶段 8。

每一批完成后都运行后端全量测试和前端生产构建。阶段 3–4 未稳定前，不开始大规模会议 UI 重构；阶段 5 未完成快照恢复前，不开放提前总结。
