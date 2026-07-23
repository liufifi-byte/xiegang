# 专家自主调研 Agent 设计规范

日期：2026-07-21

状态：待用户审阅

适用范围：Decision Council（邪杠议会）专家独立调研阶段

## 1. 决策摘要

一期采用“固定议会工作流 + 局部自治专家”架构。在外层 LangGraph 议会流程保持确定性的前提下，将当前 `parallel_research` 节点内部的固定工具调用改造成五个并行运行、状态隔离的专家 ReAct Agent。

每个专家 Agent 都能围绕用户决策目标自主判断信息缺口、选择工具及参数、读取观察结果，并决定继续调研或结束。Agent Harness 统一负责角色约束、工具权限、执行预算、超时与取消、证据账本、检查点、异常降级和实时事件追踪。

用户可以实时看到经过脱敏和结构化处理的调研轨迹，包括专家当前行动、工具名称、公开搜索词、来源摘要、证据数量和完成状态，但看不到模型原始隐式思维链、系统提示词、未经验证的草稿或私人信息。

## 2. 目标

### 2.1 产品目标

- 让等待过程成为可观看的“专家自主调研现场”，增强趣味性、信任感和传播性。
- 让职业专家的专业方法、工具选择和证据使用真实可辨，而不是只靠不同提示词进行角色扮演。
- 保持议会输出稳定、可结束、可恢复，不让局部自治破坏整体用户流程。
- 在一期免费阶段控制单场调用成本和总等待时间。

### 2.2 技术展示目标

- 实现明确的 Reason—Act—Observe—Finish 循环。
- 由模型自主选择工具、生成参数，并根据观察结果决定下一步。
- 展示完整 Agent Harness：目标契约、角色宪法、权限、预算、状态、证据、安全、追踪、降级和评估。
- 支持五个 Agent 并行、独立状态、共享原始检索缓存和单点失败隔离。
- 形成可用于作品集和简历说明的真实 Agentic Workflow，而不是将固定工作流包装成纯 Agent。

## 3. 非目标

一期不实现以下能力：

- 不把整场议会改造成完全自治系统；选人、辩论、投票、总结和归档仍由外层工作流控制。
- 不允许 Agent 修改代码、文件、提示词、工具注册表或议会流程。
- 不允许无限循环、后台长期运行、自我创建工具或自我修改角色。
- 不在独立调研期间让专家互相读取观点，避免过早从众。
- 不展示或持久化模型原始隐式思维链。
- 不为展示 Agent 感而创建没有真实外部能力的“伪工具”。
- 不在一期接入招聘、房产、金融等付费结构化数据源；后续可通过工具注册表扩展。

## 4. 架构选择

### 4.1 备选方案

1. LangGraph 预置 ReAct Agent：开发快，但 Harness、事件和终止策略较黑盒，不利于产品定制和技术展示。
2. 手写 Python 循环：控制直观，但状态检查点、恢复、图级可观测性和后续扩展较弱。
3. 显式 LangGraph ReAct 子图：将 Reason、Act、Observe、Finish 和 Forced Finalize 建成独立节点。

选择方案 3。它与现有 LangGraph 架构一致，能够清晰展示 Agent 状态机，也便于复用当前 Checkpointer、取消机制和异步并发模型。

### 4.2 总体结构

```text
外层议会图
parse_topic
  → select_experts
  → parallel_research
      → 并行实例化 5 个 ExpertResearchAgent 子图
          Reason
            ├─ tool request → Act → Observe → Reason
            ├─ finish → Finalize
            └─ budget exhausted → Forced Finalize
      → aggregate_research_results
  → debate
  → election / vote
  → summary
  → archive
```

六位职业专家共用同一套 Agent 引擎，通过 `ExpertProfile` 配置身份、专业方法、完成标准和工具权限。每场会议由外层工作流选择五位专家，五个实例拥有独立状态并并行执行。

## 5. 模块边界

### 5.1 ExpertResearchAgent

职责：执行单个专家的一次有界自主调研。

输入：

- 用户决策目标与背景简报
- 专家配置
- 用户范围内的相关历史记忆
- 可用工具注册表
- Harness 预算与安全配置
- 会议 ID、Agent ID 和取消上下文

输出：

- 结构化 `ExpertResearchResult`
- 证据账本
- 安全行动轨迹
- 运行指标与降级状态

它不负责选择其他专家、主持辩论、计算最终议会结论或直接修改数据库业务状态。

### 5.2 AgentHarness

职责：提供 Agent 运行和治理环境。

包括：

- 目标契约与角色宪法注入
- 工具白名单与参数验证
- ReAct 步数、工具次数、时间和内容预算
- 工具执行、超时、缓存和错误标准化
- 搜索隐私脱敏与网页提示注入隔离
- 证据账本、来源去重和引用约束
- Checkpointer、取消传播和强制收敛
- 用户可见轨迹事件生成
- 输出 Schema 验证与降级

Harness 不替模型选择搜索什么，也不替模型决定是否继续。它只负责确保自主行为在许可范围内可靠执行。

### 5.3 ToolRegistry

职责：注册真实工具、声明输入 Schema、设置角色权限并统一返回观察结果。

一期真实工具：

- `web_search(query, search_focus)`：通过 Tavily 搜索公开信息。
- `calculator(expression, variables)`：通过安全表达式解释器完成数值计算，不使用任意 `eval` 或代码执行。
- `memory_search(query, limit)`：通过现有 pgvector 能力检索当前用户范围内的历史决策记忆。

`finish_analysis` 是 Agent 的终止动作，不是具有外部副作用的工具。最终结果必须通过结构化 Schema 返回。

### 5.4 AgentTraceSink

职责：接收 Harness 生命周期事件，并写入现有 `parliament_events` 事件存储。

事件发送采用尽力而为策略。单条展示事件写入失败时记录日志但不终止 Agent；最终节点状态仍通过现有 `node_complete` 事件同步。

### 5.5 ResearchAggregator

职责：等待五个 Agent 完成，将结果适配为议会后续节点可读取的统一成员状态。

它只聚合最终结果，不把一个专家的初始调研过程暴露给其他专家。共享缓存仅复用清洗后的原始工具观察，不共享其他 Agent 的判断、置信度或最终结论。

## 6. 专家配置模型

每个 `ExpertProfile` 至少包含：

```text
expert_id
display_name
professional_identity
analysis_methods
required_questions
completion_criteria
forbidden_inferences
allowed_tools
public_status_vocabulary
output_emphasis
```

一期专家工具权限：

| 专家 | 专业核心 | 可用工具 |
| --- | --- | --- |
| 数学家 | 概率、决策理论、敏感性分析 | web_search、calculator、memory_search |
| 经济学家 | 机会成本、激励、边际收益、可逆性 | web_search、calculator、memory_search |
| 心理学家 | 价值、情绪、偏差、身份和关系 | web_search、memory_search |
| 历史学家 | 类比、基准概率、路径依赖和制度 | web_search、memory_search |
| 艺术家 | 问题重构、第三选项、意义和未来体验 | web_search、memory_search |
| 政治家 | 利益相关者、权力、博弈和谈判 | web_search、memory_search |

工具权限不要求实际调用。心理学家或艺术家可以判断用户背景已经足够，零工具完成本轮调研。

原来的理性、风险、机会、长期和反方五种思维模型不写入职业专家的核心身份。它们在后续固定辩论阶段作为动态议会职责分配，避免职业人物与底层方法不一致。

## 7. Agent 状态

单个 Agent 使用独立 `ExpertAgentState`：

```text
run_id
parliament_id
agent_id
expert_profile_id
goal
user_brief
category
messages
observations
evidence_ledger
safe_action_history
reason_step_count
tool_call_count
started_at
deadline_at
status
last_error
final_result
metrics
```

说明：

- `messages` 只保存模型消息、结构化动作和工具观察，不主动要求或保存长篇隐式思维链。
- `observations` 保存清洗、截断后的工具返回内容。
- `safe_action_history` 只保存可审计的动作、参数摘要、耗时和结果状态。
- `evidence_ledger` 是最终外部事实引用的唯一来源。
- 五个 Agent 使用不同 `run_id` 和独立 checkpoint namespace。

## 8. ReAct 状态机

### 8.1 Reason 节点

Reason 节点读取目标、专家宪法、用户简报、剩余预算和历史观察，输出以下两类决定之一：

```text
ToolDecision
  action = "tool"
  tool_name
  arguments
  public_intent

FinishDecision
  action = "finish"
  result
```

`public_intent` 只能使用枚举值：

- `verify_public_fact`
- `find_comparable_case`
- `quantify_tradeoff`
- `review_user_history`
- `fill_evidence_gap`

模型工具调用优先使用 `bind_tools`。如果 GLM OpenAI 兼容接口在真实集成测试中无法稳定返回工具调用，则模型适配层降级为结构化 `AgentDecision`。两条路径必须进入同一状态机、Harness 和事件协议，不能降级回程序预先替模型调用工具。

### 8.2 Act 节点

执行前依次校验：

1. 工具是否存在且属于该专家白名单；
2. 参数是否符合 Pydantic Schema；
3. 是否包含个人身份或未公开敏感信息；
4. 是否超过工具调用、时间或内容预算；
5. 查询是否与当前目标相关；
6. 是否存在可复用缓存。

通过后执行工具，并生成标准化 `ToolExecutionResult`。网络抖动允许工具适配层内部重试一次，但仍计为一次 Agent 工具行动。

### 8.3 Observe 节点

Observe 节点执行：

- 清理脚本、网页指令和无关标记；
- 将网页内容明确标记为不可信外部数据；
- 截断单次观察内容；
- 按 URL 与内容指纹去重；
- 提取标题、域名、URL、摘要和检索时间；
- 将满足最低质量条件的内容写入证据账本；
- 把标准化观察结果返回 Reason 节点。

### 8.4 Finalize 节点

最终 `ExpertResearchResult`：

```text
agent_id
stance: for | against | neutral
confidence: 0.0 - 1.0
thesis
evidence_refs[]
assumptions[]
unknowns[]
reversal_conditions[]
next_actions[]
tool_calls_used
degraded
degradation_reason
```

外部事实只能引用 `evidence_ledger` 中已经存在的 `evidence_id`。模型不能在最终结果中自由生成未经过工具账本的 URL。

### 8.5 Forced Finalize 节点

达到预算仍未结束时，关闭所有工具权限，使用现有用户背景和证据进行一次结构化强制收敛。输出必须：

- 降低置信度；
- 明确未知信息；
- 标记 `degraded=true`；
- 不得伪造缺失证据。

## 9. Harness 预算与终止条件

一期默认预算：

| 项目 | 上限 |
| --- | --- |
| 自主 Reason 决策 | 3 次 |
| 工具调用 | 1 次（一期上线默认） |
| 强制收敛调用 | 1 次，仅在未正常结束时使用 |
| 单工具超时 | 15 秒 |
| 单 Agent 总时长 | 120 秒 |
| 整体调研节点时长 | 135 秒 |
| 单次工具结果 | 最多 5 项 |
| 单次标准化观察文本 | 最多 4,000 字符 |
| 最终核心证据 | 最多 3 条 |

终止条件：

- Agent 主动返回符合 Schema 的最终结果；
- 达到 Reason、工具或时间上限，进入 Forced Finalize；
- 用户取消会议，立即传播取消信号；
- 模型或 Harness 出现不可恢复错误，输出降级中立结果；
- 外层议会超过总时限，取消所有在途 Agent。

## 10. 证据账本与共享缓存

每条 `EvidenceRecord` 至少包含：

```text
evidence_id
agent_id
tool_call_id
claim_summary
source_title
source_url
source_domain
source_excerpt
retrieved_at
relevance
reliability
strength
```

规则：

- 无 URL 的网络结果不能作为高强度外部证据。
- Agent 的价值判断、假设和推论与外部事实分开存储。
- 最终报告通过 `evidence_refs` 关联证据，不复制未经验证的来源。
- 查询缓存只在当前一场议会内共享，不跨用户、跨议会复用；缓存键至少包含脱敏查询、检索焦点和语言环境。
- 缓存共享的是标准化工具观察，不共享其他 Agent 的解释。
- `memory_search` 必须按照 `user_id` 或设备所有权隔离，不允许跨用户读取。

## 11. 实时调研事件协议

新增 SSE 事件类型 `agent_trace`。现有 `parliament_events.seq` 继续提供全局有序事件；payload 中增加 Agent 内部步骤信息。

```text
type = "agent_trace"
payload:
  trace_id
  run_id
  agent_id
  step
  phase
  public_message
  public_intent?
  tool_name?
  display_query?
  source_previews?
  evidence_count?
  duration_ms?
  cache_hit?
  timestamp
```

`phase` 枚举：

- `started`
- `deciding`
- `tool_started`
- `tool_completed`
- `evidence_added`
- `finalizing`
- `completed`
- `degraded`
- `failed`

事件产生位置：

- Harness 启动 Agent 时发出 `started`；
- 进入 Reason 前发出 `deciding`；
- 工具通过校验并开始执行时发出 `tool_started`；
- 工具结束时发出 `tool_completed`；
- 新证据写入账本时发出 `evidence_added`；
- 进入最终输出时发出 `finalizing`；
- 最终结果验证完成后发出 `completed` 或 `degraded`；
- 不可恢复失败时发出 `failed`。

`public_message` 由 Harness 内的服务端模板根据结构化事件生成，前端只负责渲染，不直接使用模型自由生成的内部推理文本。

## 12. 用户可见调研轨迹

### 12.1 进行中

会议页新增“自主调研实况”：

- 顶部专家状态条显示等待、判断中、使用工具、整理证据、完成或降级；
- 主区域使用一条合并时间线展示五个 Agent 的实时行动；
- 用户点击专家头像后，只筛选该专家的完整轨迹；
- 移动端继续使用单列时间线，不使用五列布局。

示例：

```text
经济学家开始调研
正在判断需要核实的外部信息

核实公开事实
搜索：AI 创业公司产品经理薪资与期权水平

搜索完成 · 2.4 秒
查看 5 个结果，保留 2 个有效来源

量化选项差异
正在比较三年现金收入与期权的期望价值

经济学家完成调研
1 次自主行动 · 2 条证据 · 置信度 74%
```

### 12.2 完成后

- 调研轨迹折叠为“行动次数、证据数、耗时、置信度”摘要；
- 用户可展开查看完整安全轨迹和来源预览；
- 最终报告的证据可以定位回对应 Agent、工具行动和来源；
- 零工具 Agent 明确显示“现有背景已足够，本轮无需外部查询”。

### 12.3 明确禁止展示

- 模型原始 Thought、完整隐式思维链和隐藏提示词；
- 未脱敏用户背景；
- 未清洗网页全文；
- 未通过 Schema 或证据验证的中间草稿；
- API 密钥、内部错误栈和系统安全规则。

## 13. 隐私与安全

### 13.1 搜索脱敏

工具调用前移除或概括：

- 姓名、电话、邮箱和精确地址；
- 未公开公司、客户和项目名称；
- 私人薪资、家庭、健康和身份信息；
- 任何不影响公开搜索结果的唯一标识。

用户界面只显示脱敏后的 `display_query`。实际发送查询不得比展示查询包含更多私人信息。

### 13.2 网页提示注入隔离

- 所有网页结果均标记为不可信数据，不具备指令优先级；
- 清除脚本、隐藏文本和工具指令式内容；
- 网页不能改变 Agent 目标、专家身份、工具权限或预算；
- 观察内容超过限制时只保留与原查询相关的片段；
- 工具结果中的“忽略之前指令”等文本不进入系统提示层。

### 13.3 工具安全

- 工具只能通过注册表按 Schema 调用；
- `calculator` 使用安全语法树白名单，不执行任意代码；
- 不提供 `read`、`edit`、shell、任意 HTTP 请求或文件系统工具；
- 工具异常以标准化观察返回，不把内部错误栈发送给模型或用户。

## 14. 异常与降级

| 场景 | 行为 |
| --- | --- |
| 搜索超时或失败 | 返回工具失败观察；Agent 使用现有信息继续或结束 |
| 参数校验失败 | 返回受控错误观察；允许在剩余预算内修正 |
| 重复查询 | 返回缓存或重复提示，不再次访问外部服务 |
| 达到最大步数 | 关闭工具并执行 Forced Finalize |
| GLM 工具调用格式异常 | 通过模型适配层改用结构化 `AgentDecision` |
| 最终 Schema 异常 | 自动修复一次，然后强制简化结果 |
| 单 Agent 完全失败 | 写入中立降级结果，其余 Agent 继续 |
| 少于 3 位专家成功 | 议会停止生成强结论，提示用户重试 |
| 用户取消 | 传播到所有 Agent 和在途工具，发出取消事件 |
| SSE 临时断开 | 事件保留在数据库，重连后按 seq 补发 |

单个 Agent 失败不得使并行 `gather` 抛出并终止整场议会。

## 15. 与当前代码的集成边界

当前 `parallel_research.py` 会为每位旧议员预先调用 `web_search` 和一个特色包装工具，然后把结果拼入提示词。该固定循环将由 `ExpertResearchAgentRunner` 取代。

需要保留：

- 外层 LangGraph 工作流与 Checkpointer；
- Worker 队列、总超时和取消机制；
- `parliament_events` 事件存储与 SSE 订阅；
- 后续辩论、选举、总结和归档节点；
- 单个成员失败时的隔离思路。

需要新增或调整：

- 六位职业专家配置注册表和动态五席结果；
- Expert Agent 子图与 Harness；
- 工具注册表、计算器和统一观察 Schema；
- Agent State、Result、Evidence 和 Trace Schema；
- `agent_trace` 中途事件发送，而非仅节点结束时发送；
- 前端动态专家类型和调研时间线；
- 研究结果到后续议会成员状态的适配层。

## 16. 性能与成本策略

- 五个 Agent 并行运行，不顺序等待。
- `agent_started` 应在研究节点启动后 2 秒内出现在前端，让用户立即看到活动。
- 相同脱敏查询复用共享缓存。
- 第一轮专业陈述直接复用 Agent 最终结果，不新增模型调用。
- Agent 达到两次工具调用后必须收敛，不因搜索结果不足无限重试。
- 记录每个 Agent 的 LLM 次数、工具次数、缓存命中、输入输出 Token、耗时和降级原因。
- 后续根据真实数据调整预算，但一期上限只能通过配置降低，不能由模型自行提高。

## 17. 测试设计

### 17.1 状态机单元测试

- 无工具直接完成；
- 搜索后完成；
- 搜索后计算再完成；
- 连续两次工具调用后收敛；
- 非法工具、非法参数和越权工具；
- 重复查询和缓存命中；
- 工具失败、超时和模型格式错误；
- 达到 Reason 上限进入 Forced Finalize；
- 用户取消与 Checkpointer 恢复；
- 最终证据引用必须来自账本。

### 17.2 Harness 安全测试

- 查询中的姓名、邮箱、电话、地址和内部公司名脱敏；
- 网页提示注入不能改变目标或工具权限；
- 计算器拒绝文件、网络、导入和任意代码表达式；
- `memory_search` 不能读取其他用户记录；
- 用户事件不包含原始思维链、敏感背景或内部错误栈。

### 17.3 并发与事件测试

- 五个 Agent 并发且状态互不污染；
- 单 Agent 失败不影响其他 Agent；
- `agent_trace` 顺序可恢复且不会重复渲染；
- SSE 断线重连可补齐轨迹；
- 会议取消能停止所有在途调用；
- 研究节点在总预算内结束。

### 17.4 前端测试

- 五位专家状态正确切换；
- 实时时间线、专家筛选、折叠和来源展开；
- 移动端单列显示无横向溢出；
- 零工具、缓存命中、降级、失败和取消状态文案；
- 最终证据能够回跳到对应调研事件。

## 18. Agent 评估集与验收标准

建立不少于 30 个代表性重大决策问题，覆盖职业、住房、创业、消费、关系和教育场景。

评估维度：

- 工具调用是否必要且与专家方法匹配；
- 查询是否相关、脱敏且没有无意义重复；
- 来源是否真实支持引用结论；
- 事实、假设和价值判断是否分离；
- 专业角色方法是否稳定；
- Agent 是否在预算内主动结束；
- 失败后是否诚实降低置信度；
- 用户能否从调研轨迹理解结论来源。

硬性验收：

- 循环终止率 100%；
- 工具次数越界率 0%；
- 测试隐私字段外泄率 0%；
- 外部证据引用账本覆盖率 100%；
- 单 Agent 失败不导致整场并行调研崩溃；
- 原始隐式思维链不得进入 SSE、日志或前端；
- 前后端生产构建和现有测试全部通过。

## 19. 用户价值与技术价值

### 19.1 用户价值

- 用户能看到专家主动查证、计算和停止的过程，而不是面对不可解释的等待动画。
- 来源、证据和结论可以互相追溯，增强重大决策场景的可信度。
- 专家是否使用工具、如何解释证据形成稳定的人物差异。
- 过程展示将等待时间转化为产品内容，提升演示效果和分享意愿。

### 19.2 技术价值

- 展示真正的目标驱动与 ReAct 循环，而非固定工具编排。
- 展示 Harness 对权限、预算、状态、证据、并发、安全和降级的治理能力。
- 展示确定性工作流与局部自治 Agent 的合理架构取舍。
- 保持模块化边界，未来可新增专家、真实数据工具或替换模型，而无需重写议会外层流程。

## 20. 完成后的准确项目表述

完成实现和验证后，可在作品集或简历中表述为：

> 基于 LangGraph 设计有界 ReAct 专家 Agent，通过自研 Harness 实现工具权限、执行预算、状态检查点、证据追踪、异常降级与实时可观测性，并将多个专家 Agent 并行嵌入确定性议会工作流。
