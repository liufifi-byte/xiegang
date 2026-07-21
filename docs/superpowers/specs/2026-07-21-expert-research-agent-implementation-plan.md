# 专家自主调研 Agent 实施计划

日期：2026-07-21

依据：[专家自主调研 Agent 设计规范](./2026-07-21-expert-research-agent-design.md)

## 1. 实施原则

- 保留外层 `parse_topic → research → debate → election → summary → archive` 议会工作流。
- 先完成一个可测试的经济学家 ReAct Agent 垂直切片，再扩展到五个并行专家。
- 每一步先补测试，再实现最小功能，最后运行相关测试和全量回归。
- 不依赖新的 Agent 框架；使用现有 LangGraph、LangChain OpenAI 适配层和 Pydantic。
- 不新增无真实外部能力的伪工具。一期只实现搜索、计算和记忆检索。
- 不修改或提交当前首页未提交改动；Agent 工作使用独立文件和议会页相关文件。
- 每个阶段单独提交，便于回退和审查。

## 2. 交付范围

完成后应具备：

- 显式 LangGraph Reason—Act—Observe—Finish 子图；
- 单一可复用 Agent 引擎和六位职业专家配置；
- 模型自主工具选择、参数生成、继续或停止；
- 工具白名单、预算、超时、取消、脱敏、证据账本和降级；
- 每场动态选择五位专家并行调研；
- `agent_trace` 实时 SSE 事件；
- 会议页自主调研时间线和专家详情轨迹；
- 归档后仍可查看安全调研记录；
- 后端自动化测试、前端构建和桌面/移动端浏览器验收。

## 3. 暂不交付

- 付费结构化行业数据源；
- 用户自定义工具和自定义专家；
- Agent 间在独立调研阶段互相通信；
- 原始隐式思维链展示；
- 完全自治议会；
- 长期后台 Agent 或会后自动执行任务。

## 4. 目标文件结构

```text
backend/app/agents/
  __init__.py
  schemas.py
  profiles.py
  safety.py
  evidence.py
  tool_registry.py
  trace.py
  harness.py
  model_adapter.py
  research_graph.py
  runner.py

backend/app/tools/
  calculator.py

backend/app/graph/nodes/
  select_experts.py
  parallel_research.py

backend/tests/test_agents/
  test_schemas.py
  test_profiles.py
  test_safety.py
  test_evidence.py
  test_tool_registry.py
  test_harness.py
  test_model_adapter.py
  test_research_graph.py
  test_trace.py
  test_runner.py

frontend/components/
  AgentResearchTimeline.tsx
  AgentTraceRow.tsx
```

不要求机械遵守文件数量。如果实现过程中两个文件职责高度重叠，应合并，但不得把 Profile、Harness、工具执行和前端渲染塞入单个大文件。

## 5. 分阶段实施

### Task 0：保护工作区并建立基线

**只检查，不修改功能代码。**

步骤：

1. 记录 `git status --short`，确认首页改动仍只位于：
   - `frontend/app/page.tsx`
   - `frontend/components/HomeSceneTabs.tsx`
   - `frontend/public/brand/`
2. 新建分支 `codex/expert-research-agent`，不清理、不覆盖用户工作区。
3. 运行后端基线：

   ```bash
   cd backend && pytest -q
   ```

4. 运行前端基线：

   ```bash
   pnpm --dir frontend build
   ```

5. 如果基线失败，先记录为既有问题；只有与 Agent 改造直接相关时才纳入本计划。

验收：现有测试数量和失败情况有明确记录，用户首页改动未被暂存或提交。

### Task 1：建立 Agent Schema 与职业配置

**新增：**

- `backend/app/agents/__init__.py`
- `backend/app/agents/schemas.py`
- `backend/app/agents/profiles.py`
- `backend/tests/test_agents/__init__.py`
- `backend/tests/test_agents/test_schemas.py`
- `backend/tests/test_agents/test_profiles.py`

**修改：**

- `backend/app/config.py`

测试先行：

1. 为 `AgentDecision` 测试 tool/finish 两类互斥动作。
2. 为 `ExpertResearchResult` 测试置信度范围、证据引用、降级字段和必填反转条件。
3. 为 `EvidenceRecord`、`ToolObservation`、`AgentTraceEvent` 测试 JSON 序列化。
4. 验证六个 `ExpertProfile` ID 唯一、工具权限合法、完成标准非空。
5. 验证心理学家和艺术家不被强制配置计算器。

实现：

- 定义 `ExpertAgentState` TypedDict。
- 定义 `AgentDecision`、`ToolRequest`、`ToolObservation`、`EvidenceRecord`、`ExpertResearchResult`、`AgentTraceEvent`。
- 定义六位职业专家 Profile：数学家、经济学家、心理学家、历史学家、艺术家、政治家。
- 在配置中增加可环境覆盖的预算默认值：Reason 3 次、工具 2 次、单工具 15 秒、单 Agent 60 秒、研究节点 75 秒。

验证：

```bash
cd backend && pytest tests/test_agents/test_schemas.py tests/test_agents/test_profiles.py -q
```

建议提交：`feat: define expert agent contracts and profiles`

### Task 2：实现真实工具注册表与安全计算器

**新增：**

- `backend/app/tools/calculator.py`
- `backend/app/agents/tool_registry.py`
- `backend/tests/test_agents/test_tool_registry.py`
- `backend/tests/test_agents/test_calculator.py`

**修改：**

- `backend/app/tools/web_search.py`
- `backend/app/tools/memory_query.py`

测试先行：

1. 计算器接受四则运算、括号、幂、百分比和白名单变量。
2. 计算器拒绝属性访问、导入、函数注入、文件、网络和任意代码执行。
3. 工具注册表拒绝不存在工具和专家越权工具。
4. 工具参数通过 Pydantic 校验，错误返回标准化 Observation。
5. 搜索和记忆工具输出统一为标题、URL、内容、分数和元数据。

实现：

- 使用 Python AST 白名单实现安全计算器，不使用 `eval`。
- 将现有 Tavily 搜索封装成异步可超时的工具适配器。
- 将现有 pgvector 记忆查询封装为用户隔离的 Agent 工具。
- 工具注册表根据 Expert Profile 暴露允许工具。
- 删除 Agent 路径对 `topic_specific.py` 薄包装工具的依赖；旧代码暂时保留，待切换完成后再删除。

验证：

```bash
cd backend && pytest tests/test_agents/test_calculator.py tests/test_agents/test_tool_registry.py tests/test_tools.py -q
```

建议提交：`feat: add safe expert tool registry`

### Task 3：实现隐私脱敏、观察清洗和证据账本

**新增：**

- `backend/app/agents/safety.py`
- `backend/app/agents/evidence.py`
- `backend/tests/test_agents/test_safety.py`
- `backend/tests/test_agents/test_evidence.py`

测试先行：

1. 查询中的姓名、电话、邮箱、精确地址和内部公司标识被移除或概括。
2. 展示查询与实际查询使用同一份脱敏结果。
3. 网页中的“忽略之前指令”等内容被当作不可信数据，不进入指令层。
4. 超长观察被截断到配置上限。
5. URL 和内容指纹去重有效。
6. 无 URL 的搜索结果不能成为高强度外部证据。
7. 最终 `evidence_refs` 只能引用账本已有 ID。

实现：

- `QuerySanitizer`：结构化敏感字段过滤和通用模式脱敏。
- `ObservationNormalizer`：清理标记、截断、去重和不可信内容标注。
- `EvidenceLedger`：新增、去重、限制数量、解析引用和导出。
- 搜索缓存限定为单场议会，不跨用户、跨议会复用。

验证：

```bash
cd backend && pytest tests/test_agents/test_safety.py tests/test_agents/test_evidence.py -q
```

建议提交：`feat: secure agent observations and evidence`

### Task 4：实现 Agent Trace 与 Harness 基础设施

**新增：**

- `backend/app/agents/trace.py`
- `backend/app/agents/harness.py`
- `backend/tests/test_agents/test_trace.py`
- `backend/tests/test_agents/test_harness.py`

**修改：**

- `backend/app/schemas/event.py`
- `backend/app/event_store.py`

测试先行：

1. Harness 正确计算剩余 Reason、工具和时间预算。
2. 越权、超预算和取消请求在工具执行前被拦截。
3. Trace 事件按 started、deciding、tool_started、tool_completed、evidence_added、finalizing、completed 顺序生成。
4. `public_message` 由服务端模板生成，不接受模型自由文本。
5. Trace payload 不包含原始消息、系统提示词、网页全文或隐私字段。
6. Trace Sink 写入失败不会终止 Agent。
7. `cancelled` 和 `agent_trace` 被加入合法事件类型。

实现：

- `AgentBudget` 和 deadline 检查。
- Harness 工具执行入口，统一权限、参数、超时、缓存、安全和 Observation。
- `AgentTraceSink` 使用现有 `append_event`，异步路径通过线程卸载避免阻塞事件循环。
- 每个 Agent 保留独立 `run_id`、`trace_id` 和 step。
- 安全轨迹同时写入 Agent State，便于最终归档。

验证：

```bash
cd backend && pytest tests/test_agents/test_trace.py tests/test_agents/test_harness.py tests/test_event_store.py -q
```

建议提交：`feat: add bounded agent harness and tracing`

### Task 5：实现模型动作适配层

**新增：**

- `backend/app/agents/model_adapter.py`
- `backend/tests/test_agents/test_model_adapter.py`

**修改：**

- `backend/app/llm.py`

测试先行：

1. 原生工具调用能解析成 `AgentDecision(action="tool")`。
2. 无工具调用的结构化结果能解析成 finish。
3. 原生工具格式失败后，结构化 `AgentDecision` 降级路径工作。
4. 降级仍由模型选择工具和参数，不允许程序预设查询。
5.  malformed JSON 最多修复一次，之后返回受控错误。
6. 模型返回多个工具调用时，一期只执行第一个并记录限制原因。

实现：

- `ToolCallingDecisionAdapter`：优先使用 `get_llm().bind_tools(...)`。
- `StructuredDecisionAdapter`：使用现有结构化输出容错能力。
- `ResilientDecisionAdapter`：按原生工具调用 → 结构化动作顺序降级。
- 通过依赖注入允许测试使用 Fake Model，不访问真实 GLM。

真实兼容性探针：

- 使用一个不含私人信息的测试议题调用 GLM；
- 验证其是否能稳定生成 web_search、calculator 和 finish；
- 结果记录在实施日志，不把探针写入常规自动化测试。

验证：

```bash
cd backend && pytest tests/test_agents/test_model_adapter.py tests/test_llm.py -q
```

建议提交：`feat: add resilient react decision adapter`

### Task 6：构建显式 ReAct 子图

**新增：**

- `backend/app/agents/research_graph.py`
- `backend/app/agents/runner.py`
- `backend/tests/test_agents/test_research_graph.py`
- `backend/tests/test_agents/test_runner.py`

测试先行：

使用 Fake Model 和 Fake Tools 覆盖：

1. `Reason → Finish`，零工具完成。
2. `Reason → web_search → Observe → Finish`。
3. `Reason → web_search → Observe → calculator → Observe → Finish`。
4. 重复工具查询命中缓存。
5. 非法工具后在剩余预算内修正。
6. 两次工具后仍不结束，进入 Forced Finalize。
7. 工具超时后降级完成。
8. 用户取消停止后续节点。
9. 最终外部证据引用必须来自 EvidenceLedger。
10. checkpoint 恢复后不重复已经成功的工具副作用。

实现节点：

- `reason_node`
- `act_node`
- `observe_node`
- `finalize_node`
- `forced_finalize_node`
- 条件路由 `route_decision` 和 `route_after_observe`

Runner 输入 Expert Profile、目标、用户简报、工具注册表、Trace Sink、共享缓存和取消上下文，返回 `ExpertResearchResult`、安全轨迹、证据和运行指标。

验证：

```bash
cd backend && pytest tests/test_agents/test_research_graph.py tests/test_agents/test_runner.py -q
```

建议提交：`feat: implement bounded react expert graph`

### Task 7：完成经济学家垂直切片

**新增：**

- `backend/tests/test_agents/test_economist_integration.py`

**修改：**

- `backend/app/agents/profiles.py`
- `backend/app/agents/runner.py`

场景：用户输入“要不要接受一家创业公司的 Offer”，背景包含现金薪资、期权、当前工作和风险承受力。

验收轨迹：

1. 经济学家自主选择是否搜索市场信息；
2. 自主决定是否调用计算器比较机会成本；
3. 观察结果进入证据账本；
4. 输出包含假设、未知信息和反转条件；
5. 前端事件格式所需字段齐全；
6. 不在搜索查询和 Trace 中暴露私人薪资或公司内部名称。

先使用 Fake Model 做确定性集成测试，再运行一次真实 GLM/Tavily 手工验证。真实验证失败不能通过放宽安全边界解决，应在模型适配层或提示契约中修正。

验证：

```bash
cd backend && pytest tests/test_agents/test_economist_integration.py -q
```

建议提交：`test: verify economist react vertical slice`

### Task 8：加入动态五席选择与议会状态

**新增：**

- `backend/app/graph/nodes/select_experts.py`
- `backend/tests/test_nodes/test_select_experts.py`

**修改：**

- `backend/app/graph/parliament.py`
- `backend/app/graph/state.py`
- `backend/app/graph/nodes/parse_topic.py`

测试先行：

1. 六人池每场稳定选择五人，ID 无重复。
2. 买房、Offer、跳槽、创业、消费和一般决策有确定性映射。
3. 自定义议题根据现有 category 和 briefing 维度评分选择。
4. 相同输入始终得到相同阵容和选择理由。
5. 选择失败时使用稳定默认阵容。

实现：

- 外层新增 `select_experts` 固定节点。
- 采用类别矩阵和维度关键词评分，不增加一次自由 Agent 调用。
- State 新增 `selected_expert_ids`、`expert_selection_reasons`、`agent_results` 和 `agent_traces`。
- `parliament_started` 或选择节点完成事件中带出本场阵容和选择理由。

验证：

```bash
cd backend && pytest tests/test_nodes/test_select_experts.py tests/test_state.py tests/test_graph.py -q
```

建议提交：`feat: select dynamic professional council`

### Task 9：替换并行调研节点

**修改：**

- `backend/app/graph/nodes/parallel_research.py`
- `backend/tests/test_nodes/test_parallel_research.py`
- `backend/app/llm_concurrency.py`

测试先行：

1. 按 `selected_expert_ids` 启动五个 Runner。
2. 五个 Agent 并行而不是串行执行。
3. 每个 Agent 获得独立 State、Profile 和 run_id。
4. 共享缓存只共享工具 Observation，不共享专家结论。
5. 单 Agent 失败返回中立降级结果，其余四人正常完成。
6. 少于三人成功时 research 返回明确失败状态，不进入强结论。
7. 调研结果正确适配为现有 transcript research entry。
8. Agent 安全轨迹和证据进入 State 的 `tool_calls`/`agent_results`，供归档读取。

实现：

- 删除当前程序固定遍历两个工具的逻辑。
- 使用 `asyncio.gather` 并行运行五个 `ExpertResearchAgentRunner`。
- 保留当前 semaphore、取消和硬超时控制。
- 第一轮专业陈述直接使用 Agent 最终结果，不增加一次 LLM 调用。

验证：

```bash
cd backend && pytest tests/test_nodes/test_parallel_research.py tests/test_agents -q
```

建议提交：`feat: run expert agents in parallel research`

### Task 10：让辩论、选举和总结支持动态职业专家

**修改：**

- `backend/app/graph/nodes/round_debate.py`
- `backend/app/graph/nodes/election.py`
- `backend/app/graph/nodes/summary.py`
- `backend/app/schemas/member.py`
- `backend/tests/test_nodes/test_round_debate.py`
- `backend/tests/test_nodes/test_election.py`
- `backend/tests/test_nodes/test_summary.py`

测试先行：

1. 下游节点不再依赖 `suan/chen/ji/nian/fan` 静态常量。
2. 节点从 `selected_expert_ids` 和 Profile Registry 获取名称、角色和方法。
3. 五种后台议会职责动态分配且全部覆盖一次。
4. 职业核心方法不被后台职责覆盖。
5. 缺席或降级 Agent 不被选为强势议长候选。
6. 最终总结保留专家证据引用、分歧和反转条件。

实现：

- 抽离统一 Profile Registry，删除三个节点各自复制的 `MEMBER_INFO`。
- 辩论提示改为职业身份 + 本轮议会职责。
- 将第一轮研究输出作为职业陈述；后续轮次负责交叉质询和立场修正。
- 保持现有选举机制可运行；是否合并第三轮投票作为后续独立优化，不在本次 Agent 改造中同时重写。

验证：

```bash
cd backend && pytest tests/test_nodes/test_round_debate.py tests/test_nodes/test_election.py tests/test_nodes/test_summary.py -q
```

建议提交：`feat: support professional experts across council`

### Task 11：扩展归档与查询 API

**修改：**

- `backend/app/graph/nodes/archive.py`
- `backend/app/schemas/parliament.py`
- `backend/app/api/parliament.py`
- `backend/tests/test_nodes/test_archive.py`
- `backend/tests/test_api/test_parliament_api.py`

实现策略：

- 不新增数据库列。
- `members_state` 保存最终 Agent 结果及 evidence refs。
- 现有 `tool_calls` JSONB 保存安全 Agent Trace 摘要和工具调用记录。
- `parliament_events` 保留完整实时事件。
- GET 议会接口返回选中专家、成员状态和安全调研轨迹，供总结页复现。

测试先行：

1. 归档包含阵容、Agent 结果、证据和安全轨迹。
2. 不归档原始模型消息、系统提示词和清洗前网页内容。
3. GET 接口返回动态专家及轨迹。
4. 老议会记录缺少新字段时仍能正常读取。

验证：

```bash
cd backend && pytest tests/test_nodes/test_archive.py tests/test_api/test_parliament_api.py -q
```

建议提交：`feat: archive agent research evidence and traces`

### Task 12：改造前端动态专家数据模型

**修改：**

- `frontend/lib/types.ts`
- `frontend/lib/opening_lines.ts`
- `frontend/components/MemberStatusBar.tsx`
- `frontend/components/ChatMessage.tsx`
- `frontend/app/parliament/[id]/page.tsx`
- 任何引用旧 `MemberId` 静态联合类型的组件

目标：

- 建立六位职业专家前端 Profile。
- 当前会议状态只渲染后端选中的五位。
- 状态 Record 按 selected IDs 动态初始化，不再写死五个旧 ID。
- 旧记录仍能通过 legacy Profile 显示，避免历史议会页面崩溃。
- 状态新增 `deciding`、`using_tool`、`organizing`、`degraded` 等用户可理解状态，或用 Agent Phase 映射到现有状态。

验证：

```bash
pnpm --dir frontend build
```

建议提交：`feat: render dynamic professional experts`

### Task 13：实现自主调研实况时间线

**新增：**

- `frontend/components/AgentResearchTimeline.tsx`
- `frontend/components/AgentTraceRow.tsx`

**修改：**

- `frontend/lib/types.ts`
- `frontend/lib/sse.ts`
- `frontend/app/parliament/[id]/page.tsx`
- `frontend/components/MemberStatusBar.tsx`

行为：

- 订阅 `agent_trace`，按全局 seq 去重并追加。
- 合并展示五位专家的实时行动。
- 支持点击头像过滤单个专家。
- 展示脱敏搜索词、工具、耗时、缓存命中、来源标题和证据数量。
- 默认不展示网页全文和模型原始消息。
- Agent 完成后折叠为行动次数、证据数、耗时和置信度摘要。
- 零工具 Agent 显示“现有背景已经足够，本轮无需外部查询”。
- 断线重连时按 seq 补齐，不重复显示。

视觉约束：

- 延续当前首页米色、白色/浅米色卡片和紫红品牌色。
- 会议页采用单列时间线，移动端不出现横向滚动。
- 过程展示清晰但不抢过专家最终观点。

验证：

```bash
pnpm --dir frontend build
```

浏览器验收必须使用浏览器控制技能：

- 桌面宽度：调研开始、工具调用、来源展开、完成折叠；
- 390px 移动端：单列时间线、抽屉、长搜索词换行；
- SSE 断线重连；
- 单 Agent 失败与全局取消；
- 五位专家并发事件交错时顺序稳定。

建议提交：`feat: show live expert research traces`

### Task 14：总结页证据回溯

**修改：**

- `frontend/app/summary/[id]/page.tsx`
- `frontend/components/SummaryPanel.tsx`
- `frontend/lib/types.ts`

行为：

- 总结页读取选中专家、Agent 结果和安全轨迹。
- 外部证据显示来源标题、域名和链接。
- 点击证据可展开“由哪位专家、在哪次工具行动中获得”。
- 推断、假设和外部事实使用不同视觉标签。
- 降级结果明确显示“信息不足”及缺失项。

验证：

```bash
pnpm --dir frontend build
```

浏览器检查刷新后仍能看到来源与研究过程。

建议提交：`feat: link council conclusions to research evidence`

### Task 15：全量测试、评估集和生产验收

**新增：**

- `backend/tests/evals/agent_cases.json`
- `backend/tests/evals/test_agent_eval_contract.py`
- 可选的人工评估记录文档，不纳入常规 CI 的真实 API 调用

评估集至少 30 个问题，覆盖：

- Offer、跳槽和创业；
- 买房、消费和投资；
- 教育、关系和家庭；
- 信息完整与严重缺失；
- 不需要搜索和必须搜索；
- 容易触发隐私泄露或网页提示注入的对抗样例。

硬性自动化验收：

- 循环终止率 100%；
- 工具次数越界率 0%；
- 测试隐私字段外泄率 0%；
- 外部证据账本引用率 100%；
- 单 Agent 失败不导致整场研究崩溃；
- 原始思维链不进入事件、API 或归档；
- 后端全量测试通过；
- 前端生产构建通过。

命令：

```bash
cd backend && pytest -q
pnpm --dir frontend build
```

真实环境验收：

1. 使用六类代表性议题各运行一场会议。
2. 记录每场 Agent 数、LLM 次数、工具次数、缓存命中、首事件时间、总耗时和降级情况。
3. 抽查所有外部证据链接是否支持对应观点。
4. 检查用户隐私是否进入公开搜索查询。
5. 浏览器完成桌面和移动端视觉验收。

最终建议提交：`feat: complete observable expert agent workflow`

## 6. 实施顺序与检查点

### 检查点 A：Agent 内核可用

完成 Task 1—7。此时经济学家可以在测试环境完成真实 ReAct 循环，但尚未替换生产议会研究节点。

必须确认：

- 模型确实自主选择工具；
- Harness 能限制权限与预算；
- Trace 不包含隐式思维链和私人信息；
- GLM 原生工具调用或结构化动作降级至少一条路径稳定。

### 检查点 B：后端议会完成迁移

完成 Task 8—11。此时五位职业专家可以并行调研，后续辩论、选举、总结和归档正常运行。

必须确认：

- 没有旧静态五议员 ID 遗留在运行路径；
- 单 Agent 失败不会拖垮议会；
- 旧历史记录仍可读取；
- 全量后端测试通过。

### 检查点 C：用户可观察体验完成

完成 Task 12—15。用户可以实时观看安全调研轨迹，并在最终报告追溯证据。

必须确认：

- 桌面和移动端均清晰；
- 第一条 Agent 活动事件及时出现；
- SSE 重连不会重复轨迹；
- 调研过程增强信任，不暴露内部推理；
- 真实调用成本和耗时在配置上限内。

## 7. 回滚策略

- 在 `parallel_research` 接入前保留旧研究路径，通过服务端配置开关选择 `legacy` 或 `react_agent`。
- 内核和真实模型验证通过后再把默认值切到 `react_agent`。
- 如果 Agent 路径在生产中连续失败，切回 legacy 不影响辩论、总结和历史数据读取。
- 旧路径只在新路径通过真实环境验收后删除；删除作为独立提交，不与首次切换混在一起。

## 8. 完成定义

只有同时满足以下条件，才算完成：

- 五个专家实例通过显式 ReAct 子图自主选择工具、参数和终止时间；
- Harness 对权限、预算、超时、取消、脱敏、证据和降级全部生效；
- 用户实时看到安全调研过程，且归档后仍可回看；
- 动态职业专家贯穿调研、辩论、选举、总结和报告；
- 30 个评估样例合同通过；
- 后端全量测试和前端生产构建通过；
- 浏览器完成桌面与 390px 移动端验收；
- 当前首页用户改动得到保留，没有被 Agent 提交覆盖。
