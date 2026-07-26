# 双层专家议会实施计划

日期：2026-07-26  
依据：[双层专家议会产品设计](./2026-07-26-dual-layer-expert-council-design.md)

## 实施原则

- 保留现有会议创建、Worker、SSE、刷新恢复和取消机制；
- 先建立数据契约，再替换议会编排，最后调整页面；
- 每个阶段先补测试，再修改实现；
- 兼容既有会议归档，新增字段均提供默认值；
- 最终总结页不读取、不传递、不展示 Agent 详细工作路径；
- 首期只实现有边界的局部自治，不增加无限轮次或自由工具创建。

## 阶段一：建立决策与证据数据契约

### 目标

把当前混合的 `evidence/confidence/reasoning_summary` 拆成可验证的决策信息。

### 后端改动

- 扩展 `backend/app/agents/schemas.py`
  - 新增 `EvidenceKind`：`user_fact | web_source | calculation | inference`；
  - 扩展证据记录的来源时间、支持的判断、输入、公式和假设；
  - 将专家结果改为 `position`、`basis_level`、`thesis`、`risks`、`evidence_refs`、`assumptions`、`unknowns`、`reversal_conditions`、`next_actions`；
  - 保留 `confidence` 仅作为旧归档兼容字段，不再进入新流程判断。
- 扩展 `backend/app/graph/state.py`
  - 增加 `decision_model`、`evidence_pool`、`core_disputes`、`round_decisions`、`basis_assessment`；
  - 增加最少轮次2和最大轮次3；
  - 增加中立主持人状态，不再依赖主席选举。
- 改造 `backend/app/agents/evidence.py`
  - 四类证据统一入池；
  - 提供证据引用校验；
  - 搜索失败时不得生成网页证据；
  - 计算结果必须包含输入与公式。
- 改造 `backend/app/agents/harness.py`
  - 最终校验精确外部数据是否具备网页或计算依据；
  - 无依据时将表述降级为推断假设；
  - 生成系统判断基础，替代模型自报置信度。

### 测试

- `backend/tests/test_agents/test_schemas.py`
- `backend/tests/test_agents/test_evidence.py`
- `backend/tests/test_agents/test_harness.py`
- `backend/tests/test_state.py`

验收：

- 四类证据能够序列化、归档和引用；
- 未知证据引用被拒绝；
- 搜索失败不会产生伪网页来源；
- 精确计算缺少输入或公式时被拒绝；
- 新会议不使用模型自报置信度决定质量。

## 阶段二：深化人物与动态议会职责

### 目标

为六位职业专家建立“专业内核＋稳定性格＋动态职责”。

### 后端改动

- 改造 `backend/app/agents/profiles.py`
  - 为六位专家增加专业边界、表达特征、标志性问题和禁用表达；
  - 职业内核保持稳定；
  - 议会职责不永久绑定职业。
- 改造 `backend/app/graph/nodes/select_experts.py`
  - 根据决策模型和专家适配度选择默认五位；
  - 为本场分配证据审计、下行风险、机会探索、长期影响、反方质询五项职责；
  - 避免心理学家固定为保守派、艺术家固定为机会派等错配。
- 更新 Agent 提示词
  - 先输出专业判断，再按动态职责提出质询；
  - 禁止为了职责改变专业结论；
  - 禁止所有专家使用相同报告模板。

### 测试

- `backend/tests/test_agents/test_profiles.py`
- `backend/tests/test_nodes/test_select_experts.py`

验收：

- 同一专家在不同会议可承担不同职责；
- 每项职责都有适配专家；
- 职业内核和临时职责不存在明显冲突；
- 专家发言能够从内容上区分。

## 阶段三：决策建模、公共证据池与独立初判

### 目标

先区分用户事实、偏好、未知条件和外部信息缺口，再让专家互不可见地独立判断。

### 后端改动

- 改造 `backend/app/graph/nodes/parse_topic.py`
  - 输出备选方案、用户事实、偏好、未知条件和待核实事项；
  - 将用户事实写入公共证据池；
  - 解析失败时使用确定性兜底。
- 改造 `backend/app/graph/nodes/parallel_research.py`
  - 阶段拆为独立初判和针对性调研；
  - 专家初判互不可见；
  - 工具按信息缺口调用，不强制每位专家搜索；
  - 相同检索问题复用公共证据；
  - 完成一位即发布一位。
- 改造网页搜索和计算器适配
  - 网页结果必须产生合法的 `web_source`；
  - 计算器必须产生合法的 `calculation`；
  - 工具失败转换为产品语言并继续条件分析。

### 测试

- `backend/tests/test_nodes/test_parse_topic.py`
- `backend/tests/test_nodes/test_parallel_research.py`
- `backend/tests/test_agents/test_research_graph.py`
- `backend/tests/test_tools.py`

验收：

- 用户信息不会被混入推断；
- 相同事实不被五位专家重复搜索；
- 无需外部信息的专家可以不调用搜索；
- 一位专家失败不影响其他专家完成。

## 阶段四：替换为两至三轮争议驱动议会

### 目标

删除“选三位只质询一轮”和“按置信度选主席”的旧主流程。

### 新节点

- `backend/app/graph/nodes/opening_round.py`
  - 第一轮五位专家全部发言；
  - 输出倾向、理由、依据、风险和拟质询对象。
- `backend/app/graph/nodes/extract_disputes.py`
  - 最多提取三个核心争议；
  - 标记事实、推理或价值争议；
  - 无真实分歧时创建反共识压力测试。
- `backend/app/graph/nodes/cross_examination_round.py`
  - 第二轮按争议邀请二至四人；
  - 强制产生新证据、新推理、新方案或立场变化。
- `backend/app/graph/nodes/decide_third_round.py`
  - 依据新证据、立场变化、用户输入、未解决争议和过早共识决定是否进入第三轮。
- `backend/app/graph/nodes/convergence_round.py`
  - 第三轮只处理剩余分歧、翻转条件和下一步行动。
- `backend/app/graph/nodes/moderator_summary.py`
  - 中立总结，不选议长；
  - LLM失败时使用结构化字段生成基础总结。

### 编排改动

- 重写 `backend/app/graph/parliament.py`
  - `parse_topic → select_experts → independent_research → opening_round → extract_disputes → cross_examination_round → decide_third_round → [convergence_round] → moderator_summary → archive`
  - 第一、二轮必走；
  - 第三轮条件触发；
  - 保留用户中断与恢复机制。
- 旧节点保留用于读取历史归档，但不进入新会议主流程：
  - `select_divergent_experts.py`
  - `cross_examination.py`
  - `select_chair.py`
  - `election.py`

### 测试

- 新增各节点单元测试；
- 更新 `backend/tests/test_graph.py`；
- 更新 API、Worker恢复、取消和中断测试。

验收：

- 正式讨论至少两轮、最多三轮；
- 第一轮五位专家全部参与；
- 第二轮围绕争议而不是按分数选三人；
- 第三轮触发条件可测试且确定；
- 总结不依赖主席或自报置信度；
- 任一轮部分失败仍可总结。

## 阶段五：会议进行页的信息层级

### 目标

默认展示会议推进和正式发言，内部轨迹收进独立入口。

### 前端改动

- 更新 `frontend/lib/types.ts` 和 `frontend/lib/sse.ts`
  - 增加决策模型、职责、核心争议、判断基础和观点变化类型；
  - 保留旧字段兼容历史会议。
- 改造 `frontend/app/parliament/[id]/page.tsx`
  - 阶段文案改为用户语言；
  - 展示两至三轮进度；
  - 五位专家完成一位展示一位；
  - 用户可在第一轮和第二轮后介入。
- 改造 `frontend/components/ChatMessage.tsx`
  - 移除 `R0/R1` 和置信度百分比；
  - 展示“第1轮/第2轮/第3轮”、动态职责、倾向、判断基础和回复关系；
  - 分析依据按四类收起展示。
- 新增核心争议组件
  - 展示争议双方、争议类型、状态和定位能力。
- 保留 `AgentResearchTimeline`
  - 仅在会议进行中通过“Agent运行记录”展开；
  - 默认收起；
  - 不作为会议主内容。

### 测试

- TypeScript类型检查；
- 关键组件渲染测试（若项目现有测试框架可用）；
- 手工验证刷新、滚动、最新消息和用户介入。

## 阶段六：最终总结页重构

### 目标

总结页只展示决策结果和整理后的依据，完全隐藏详细 Agent 工作路径。

### 前端改动

- 改造 `frontend/app/summary/[id]/page.tsx`
  - 不读取 `agent_traces`；
  - 不向 `SummaryPanel` 传递轨迹；
  - 仅请求总结所需的归档字段。
- 改造 `frontend/components/SummaryPanel.tsx`
  - 删除 `AgentResearchTimeline`；
  - 删除工具行动次数、耗时、ReAct步骤和内部状态；
  - 删除议长选举展示；
  - 顶部展示一分钟结论；
  - 完整报告展示共识、分歧、方案代价、事实、假设、下一步和翻转条件；
  - 分析依据只展示整理后的四类证据。
- 更新 API 返回策略
  - 总结页接口或查询参数默认不返回 Agent trace；
  - 旧会议仍能通过兼容映射显示基础纪要。

### 验收

- 最终总结页面代码不导入 Agent trace 组件和类型；
- 页面不出现工具次数、运行耗时、Reason/Act/Observe或内部错误；
- 网页来源仍可打开；
- 用户信息、计算和推断清晰分组；
- 历史会议能够正常打开。

## 阶段七：端到端稳定性与产品验收

### 自动验证

- 运行后端完整测试；
- 运行前端类型检查和构建；
- 增加买房、接Offer、婚姻和创业四类评估用例；
- 检查精确数字和来源契约；
- 检查二至三轮边界。

### 本地端到端验证

1. 创建买房会议并确认五位专家陆续完成；
2. 确认第一轮五人全部发言；
3. 确认第二轮围绕一至三个争议；
4. 分别验证需要和不需要第三轮的会议；
5. 验证用户在轮次间补充信息；
6. 验证搜索失败、单专家超时、总结超时；
7. 刷新会议页并恢复状态和阅读位置；
8. 打开总结页并确认不存在Agent工作路径；
9. 检查分享结果默认脱敏。

## 提交顺序

1. `feat: add decision evidence contracts`
2. `feat: deepen expert identities and council duties`
3. `feat: build shared evidence research flow`
4. `feat: run dispute-driven council rounds`
5. `feat: redesign live council information hierarchy`
6. `feat: simplify final decision summary`
7. `test: verify dual-layer council end to end`

每个提交必须保持测试通过，避免将后端状态迁移、流程替换和前端重构混为一个不可验证的大提交。
