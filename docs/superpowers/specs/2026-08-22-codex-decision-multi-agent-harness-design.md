# 深墨决策台：基于 Codex Harness 的多 Agent 决策系统完整方案

> 日期：2026-08-22  
> 状态：已完成产品方向确认，交付 ZCode 实施  
> 目标版本：Decision Agent Runtime V4  
> 核心原则：确定性 Workflow 外壳 + 主 Agent Loop + 子 Agent Loop + 争议驱动 Debate Loop

---

## 1. 执行摘要

深墨决策台不再把一次会议实现为“固定节点依次调用若干角色 Prompt”，而应升级为一个有界、可恢复、可审计的多 Agent 决策系统。

目标架构以 Codex App Server 提供的 Thread、Turn、事件流、工具调用、暂停恢复、上下文压缩和审批能力为运行内核，在其上增加深墨决策台自己的领域 Harness：

1. 一个持续存在的主 Agent（决策议长）负责建模、规划、委派、交互、收敛和最终建议；
2. 主 Agent 根据问题与用户选择，从八位专家中选择 3—5 位子 Agent；
3. 每个子 Agent 运行自己的受限专业 Loop，并拥有独立上下文、工具权限和退出条件；
4. 第一轮分析完成后，主 Agent只围绕实质分歧组织定向辩论；
5. 调研 Agent 建立事实底座，证据审计 Agent 检查来源、时效、引用和推断边界；
6. 外层 Workflow 强制执行阶段门、预算、持久化、故障隔离和最终输出契约；
7. 单个专家、搜索或引用失败不得导致整场会议失败；预算耗尽时输出阶段性结论，而不是显示“议会失败”。

迁移采用 `legacy / shadow / codex_harness` 三模式，不直接删除现有 LangGraph。先完成一条端到端垂直切片，通过质量、稳定性、成本和延迟评测后，再逐步切换生产流量。

---

## 2. 背景与当前问题

### 2.1 当前已有能力

项目当前已经具备：

- FastAPI + 独立 Worker；
- PostgreSQL/Supabase 持久化；
- LangGraph checkpointer；
- Worker 写事件、SSE 读事件的断线重连机制；
- 八位专家 Profile；
- 单专家 `reason → act → observe → finalize` ReAct 子图；
- 工具白名单、搜索、计算和用户记忆；
- Agent 预算、超时、取消和结构化输出；
- Evidence Ledger 和公开 Trace；
- 两步会前确认与用户补充信息；
- 局部失败后的降级结果。

### 2.2 当前核心限制

当前外层流程仍然是固定的：

```text
parse_topic
→ select_experts
→ clarification_gate
→ research
→ opening_round
→ extract_disputes
→ cross_examination
→ optional convergence
→ chair_endorsement
→ summary
→ archive
```

主要问题：

1. 主流程没有真正的规划、观察和重新规划能力；
2. 专家虽然拥有局部 ReAct，但收到的任务区分度不足；
3. 第一轮研究、公开观点和后续辩论之间的上下文边界不清晰；
4. 辩论按轮次推进，而不是按“会改变结论的争议”推进；
5. 搜索结果质量控制发生得太晚，搜索摘要和低质量来源容易进入专家文本；
6. 总超时仍可能把本可产出部分结果的会议标记为失败；
7. 用户插话只能等待既定节点读取，不能成为主 Agent 下一步行动的一部分；
8. 当前 Summary 更接近汇总器，而不是对最终建议负责的主 Agent；
9. 上下文主要以 transcript 和大字典累积，缺少按 Agent 编译的 Context Packet；
10. 缺少统一的主 Agent Finalization Gate。

---

## 3. 目标与非目标

### 3.1 目标

- 支持主 Agent 自主规划、委派、追问、重新规划和结束；
- 支持 3—5 个专业子 Agent 并行执行独立 Loop；
- 支持调研、证据审计等服务型 Agent；
- 支持主 Agent 在执行和辩论中向子 Agent 发送定向任务；
- 支持用户在任何安全检查点补充信息；
- 支持跨请求、跨进程恢复；
- 支持结构化上下文压缩和事实来源追踪；
- 支持局部失败隔离、重试和阶段性完成；
- 支持基于事件的前端过程展示；
- 支持旧流程并行运行和可回滚发布。

### 3.2 非目标

- 不让八位专家每场全部运行；
- 不让 Agent 自由无限聊天；
- 不公开模型隐藏思维过程；
- 不允许专家自由访问 Shell 或修改服务器文件；
- 不用多数投票替代证据和专业相关性；
- 不在第一阶段删除 LangGraph、现有专家 Profile 或现有 API；
- 不在第一阶段做外部写操作，例如代替用户签约、发邮件或执行交易；
- 深墨维护一个固定版本的 Codex 下游 Fork 作为 Agent Runtime 内核；领域能力优先通过
  扩展点实现，不以散改 Codex Core 作为主要集成方式（详见 `docs/adr/ADR-codex-downstream-harness.md`）。

---

## 4. 总体架构

```text
┌───────────────────────────────────────────────────────────────┐
│                         Web UI                                │
│  任务确认 / 专家选择 / 实时进度 / 用户补充 / 最终决策报告       │
└───────────────────────────┬───────────────────────────────────┘
                            │ HTTP + SSE
┌───────────────────────────▼───────────────────────────────────┐
│                     FastAPI Business API                     │
│  用户 / 会议 / 权限 / 幂等 / 报告 / 反馈 / Runtime Feature Flag │
└───────────────────────────┬───────────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────────┐
│                   Decision Harness Control Plane             │
│ Session │ Workflow │ Scheduler │ Context │ Policy │ Budget    │
│ Events  │ Recovery │ Finalization Gate │ Model Routing        │
└───────────────┬───────────────────────┬───────────────────────┘
                │                       │
       ┌────────▼────────┐     ┌────────▼────────────────┐
       │ Codex App Server │     │ Legacy GLM Agent Runner │
       │ Root/Child Thread│     │ 兼容、降级、Shadow       │
       └────────┬────────┘     └─────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────┐
│                       Agent Collection                       │
│ 主 Agent │ 调研 Agent │ 八位专家 Agent │ 证据审计 Agent          │
└───────────────┬───────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────┐
│                     Domain Tool Gateway                      │
│ Search │ Fetch │ Evidence │ Calculator │ Memory │ Ask User    │
│ Delegate Expert │ Debate │ Audit │ State Read/Write            │
└───────────────┬───────────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────────┐
│ PostgreSQL: 业务事实、计划、Agent Run、证据、事件、检查点        │
└───────────────────────────────────────────────────────────────┘
```

### 4.1 设计边界

- Codex Harness 管“Agent 如何持续工作”；
- Decision Harness 管“决策会议允许如何工作”；
- Workflow 管“当前在哪个阶段、是否允许跨阶段”；
- 主 Agent 管“下一步最有价值的认知行动是什么”；
- 专家 Agent 管“如何完成被分配的专业问题”；
- Evidence Kernel 管“哪些事实可以进入最终结论”；
- 产品数据库是业务事实源，Codex Thread 不是唯一事实源。

---

## 5. Agent 集合

### 5.1 主 Agent：Decision Chair

主 Agent 是唯一持续存在并对最终建议负责的 Root Agent。

职责：

- 解析用户决策；
- 建立 Decision Model；
- 生成和更新 PlanSpec；
- 判断是否需要用户补充；
- 选择专家和生成任务包；
- 观察所有子任务；
- 提取实质分歧；
- 发起定向辩论；
- 请求证据补充或修订；
- 综合结论；
- 提议结束会议。

主 Agent 不得：

- 用投票数量直接决定建议；
- 把未审计数字作为核心依据；
- 在专家独立分析前泄露其他专家立场；
- 跳过阻断性用户信息并伪装成确定结论；
- 自行绕过预算和阶段门。

### 5.2 调研 Agent：Research Lead

调研 Agent 是服务型子 Agent，不表达支持或反对。

职责：

```text
拆解待核实命题
→ 规划来源类型
→ 检索候选来源
→ 打开原始页面
→ 检查发布日期、作者和权威性
→ 交叉验证
→ 写入 Evidence Ledger
```

输出必须是 EvidenceRecord 集合，不输出长篇观点。

### 5.3 八位专家 Agent

| ID | 角色 | 核心问题 | 默认工具 |
|---|---|---|---|
| economist | 经济学家 | 考虑全部代价后值不值得 | evidence_search、calculator、memory |
| psychologist | 心理学家 | 是否符合用户动机、价值与承受能力 | memory，默认不搜网页 |
| historian | 历史学家 | 类似路径如何演变，类比边界是什么 | evidence_search、memory |
| mathematician | 数学家 | 哪些变量、概率和阈值决定结果 | calculator、evidence_search |
| strategist | 战略家 | 下一步怎么走，如何保留选择权 | memory、evidence_search |
| legal_scholar | 法学家 | 权利、义务、程序和合同边界是什么 | authoritative_search、document_read |
| sociologist | 社会学家 | 家庭、组织、关系与环境如何影响结果 | memory、evidence_search |
| contrarian | 逆向思考者 | 是否问错问题，是否存在第三方案 | memory，按需 evidence_search |

每场系统推荐 3—5 位，用户拥有最终阵容决定权。用户固定阵容时，主 Agent只能在获得用户同意后新增角色；服务型调研和审计 Agent 不计入公开专家席位。

### 5.4 证据审计 Agent：Evidence Auditor

职责：

- Claim 与 Evidence 是否对应；
- 来源是否满足领域权威性要求；
- 信息是否过期；
- 是否将假设写成事实；
- 是否出现伪精确数字；
- 是否存在相互冲突的来源；
- 是否越过法律、医疗、金融等边界；
- 最终报告中的关键引用是否可打开。

审计输出为问题列表和定向修订任务，不直接重写整份报告。

---

## 6. 外层 Workflow

外层 Workflow 保持确定性，阶段内部允许 Agent 循环。

```text
INTAKE
  ↓
PLANNING_LOOP
  ↓
USER_CLARIFICATION（可选，持久化暂停）
  ↓
DELEGATION
  ↓
EXPERT_LOOPS（并行）
  ↓
DISPUTE_EXTRACTION
  ↓
DEBATE_LOOP（按需）
  ↓
EVIDENCE_AUDIT
  ├── 不通过 → TARGETED_REPAIR
  └── 通过
        ↓
SYNTHESIS_LOOP
  ↓
FINALIZATION_GATE
  ├── 需要用户 → WAITING_USER
  ├── 可修订 → SYNTHESIS_LOOP
  ├── 预算耗尽 → PARTIAL_FINALIZED
  └── 通过 → FINALIZED
```

### 6.1 Workflow 与 LLM 分工

| LLM 决定 | Workflow 强制 |
|---|---|
| 如何理解问题 | 当前阶段 |
| 推荐哪些专家 | 用户固定的专家边界 |
| 下一步最有价值的行动 | 可选 Action 枚举 |
| 哪些观点构成实质分歧 | 最大辩论轮数 |
| 是否需要重新规划 | 最大 Replan 次数 |
| 是否需要询问用户 | 暂停与恢复协议 |
| 最终建议与反转条件 | Evidence Audit 必须通过 |
| 建议何时结束 | Finalization Gate 最终裁决 |

---

## 7. 主 Agent Loop Engineering

### 7.1 标准循环

```text
OBSERVE
→ PLAN / REPLAN
→ SELECT_ACTION
→ ACT
→ OBSERVE_RESULT
→ EVALUATE
→ CONTINUE / PAUSE / PROPOSE_FINALIZE
```

主 Agent每一轮只允许选择一个高层动作：

```text
ask_user
delegate_agent
send_followup
request_research
start_debate
request_revision
run_evidence_audit
revise_plan
finalize
```

### 7.2 Decision Model

```json
{
  "question": "是否从当前公司跳槽到一家 AI 初创公司",
  "options": ["留任", "立即跳槽", "暂缓并谈判"],
  "user_goals": ["职业成长", "提高长期收益", "控制家庭风险"],
  "constraints": ["竞业风险", "家庭现金流"],
  "verified_user_facts": [],
  "preferences": [],
  "unknowns": [],
  "decision_deadline": null,
  "risk_level": "medium"
}
```

### 7.3 PlanSpec

```json
{
  "plan_version": 1,
  "objective": "给出当前条件下的跳槽建议和行动顺序",
  "research_tasks": [],
  "expert_tasks": [],
  "expected_conflicts": [],
  "blocking_unknowns": [],
  "non_blocking_unknowns": [],
  "stop_conditions": [],
  "budget_allocation": {},
  "created_from_state_version": 1
}
```

Plan 允许更新，但历史版本必须保留。Replan 必须给出机器可读原因：

- `new_user_fact`
- `evidence_conflict`
- `expert_failure`
- `new_option_found`
- `material_disagreement`
- `budget_pressure`
- `audit_rejection`

### 7.4 主 Agent 每轮评估

- 目标是否仍然正确；
- 计划是否覆盖所有重要方案；
- 是否出现阻断性信息缺口；
- 是否需要新增、缩小或取消任务；
- 专家结论是否真正回答所分配问题；
- 分歧是否会改变最终建议；
- 证据是否充分且可核验；
- 继续行动的边际价值是否高于成本；
- 是否满足退出条件。

### 7.5 主 Agent 退出条件

正常完成必须同时满足：

```text
objective_answered
AND options_covered
AND critical_gaps_handled
AND material_conflicts_handled
AND evidence_audit_passed
AND recommendation_actionable
AND no_pending_high_priority_task
AND output_contract_valid
```

主 Agent只能“提议结束”，由 Finalization Gate 检查任务队列、审计状态、预算和输出结构。

退出状态：

| 状态 | 含义 |
|---|---|
| FINALIZED | 完整通过退出条件 |
| PARTIAL_FINALIZED | 预算耗尽，但已有安全、有限的阶段性结论 |
| WAITING_USER | 缺失用户信息会导致建议反转，暂停等待 |
| CANCELLED | 用户主动停止，保留已完成结果 |
| FAILED | 只有核心运行时无法恢复时使用 |

### 7.6 防无限循环默认预算

- 主 Agent 最大高层动作：12；
- 最大 Replan：3；
- 同一信息缺口最多检索：2轮；
- 同一争议最多辩论：2轮；
- 同一失败任务最多重试：2次；
- 连续两轮没有新增事实、分歧、方案或行动时触发收敛；
- 用户等待时间不计入 Agent Runtime；
- 总运行时间与 HTTP/SSE 连接解耦；
- 达到硬预算后进入 `PARTIAL_FINALIZED`，不得直接标记会议失败。

这些是默认值，必须配置化并记录实际用量。

---

## 8. 子 Agent Loop Engineering

### 8.1 标准循环

```text
ORIENT
→ ASSESS_INFORMATION
→ SELECT_TOOL_OR_ANALYZE
→ OBSERVE
→ UPDATE_PROFESSIONAL_VIEW
→ SELF_REVIEW
→ CONTINUE / REQUEST_CLARIFICATION / FINALIZE
```

子 Agent可选动作：

```text
inspect_context
search_evidence
calculate
query_memory
request_clarification
draft_judgment
self_review
finalize
```

### 8.2 ExpertTaskPacket

```json
{
  "task_id": "task_xxx",
  "expert_id": "economist",
  "objective": "比较三个方案的机会成本与风险收益",
  "decision_options": [],
  "relevant_user_facts": [],
  "relevant_evidence": [],
  "known_unknowns": [],
  "role_contract": {},
  "allowed_tools": [],
  "forbidden_actions": [],
  "expected_output": {},
  "deadline_at": null,
  "budget": {},
  "context_version": 1
}
```

每个任务必须是具体专业问题，禁止只发送“请分析这个问题”。

### 8.3 子 Agent 输出

在现有 `ExpertResearchResult` 基础上升级：

```json
{
  "task_id": "task_xxx",
  "agent_id": "economist",
  "status": "completed",
  "relevance": 0.9,
  "stance": "cautious",
  "judgment": "",
  "analysis_points": [],
  "evidence_refs": [],
  "assumptions": [],
  "unknowns": [],
  "action": "",
  "reversal_conditions": [],
  "questions_for_other_agents": [],
  "questions_for_user": [],
  "confidence": 0.0,
  "completion": {
    "objective_answered": true,
    "evidence_checked": true,
    "boundary_checked": true
  }
}
```

### 8.4 子 Agent 退出

正常退出：

- 完成被分配的专业目标；
- 关键判断和证据能够对应；
- 未把未知写成事实；
- 未越过职业边界；
- 给出结论反转条件；
- 继续工具调用预计不会显著改变判断。

预算退出：返回 `partial` 结果，列出完成项、缺失项和安全结论。单个子 Agent不得因预算耗尽抛出导致全局失败的异常。

---

## 9. Debate Loop Engineering

### 9.1 原则

- 第一轮必须独立分析，专家看不到其他专家观点；
- 辩论只围绕会影响建议的实质分歧；
- 不允许全员自由群聊；
- 每个争议最多两轮；
- 允许保留分歧，不强制一致；
- 主 Agent是主持人和任务路由器，不用自己的观点压制专家。

### 9.2 DisputeIssue

```json
{
  "dispute_id": "dispute_1",
  "question": "是否应在股权条款未确认前接受 Offer",
  "importance": 0.9,
  "conflict_type": "assumption_weight",
  "claims": [],
  "participants": ["economist", "strategist"],
  "shared_evidence_refs": [],
  "missing_variable": "Offer 是否允许延期",
  "status": "open",
  "round": 0
}
```

`conflict_type`：

- `fact_conflict`
- `assumption_conflict`
- `value_conflict`
- `time_horizon_conflict`
- `risk_tolerance_conflict`
- `option_definition_conflict`

### 9.3 辩论流程

```text
主 Agent提取 Claim A / Claim B
→ 判断是否为实质分歧
→ 生成定向质询
→ 被质询专家回应
→ 对方确认、反驳或修正
→ 主 Agent判断是否解决
→ 必要时补充证据或询问用户
→ 关闭或保留分歧
```

专家每轮返回：

```json
{
  "position_before": "conditional_support",
  "position_after": "defer",
  "changed": true,
  "reason": "",
  "new_evidence_refs": [],
  "remaining_disagreement": "",
  "needs_user_input": false
}
```

### 9.4 辩论停止条件

- 分歧已解决；
- 分歧已定位为不同假设；
- 分歧已定位为用户价值偏好；
- 已缩小为一个待确认变量；
- 一方依据明显无效并已修订；
- 连续一轮没有新增信息；
- 达到两轮上限。

---

## 10. 主 Agent 的交互能力

### 10.1 与用户交互

只有以下情况主动打断：

- 缺失信息会使建议反转；
- 用户目标之间存在必须由用户选择的价值冲突；
- 涉及隐私、敏感数据或高风险工具权限；
- 用户明确要求过程中参与。

普通未知信息不打断，使用情景分析。

### 10.2 与子 Agent交互

主 Agent可以：

- 创建任务；
- 发送 Follow-up；
- 缩小或重写任务目标；
- 要求补充证据；
- 要求删除无法核验的数字；
- 将一个专家的问题转交另一位专家；
- 暂停低价值搜索；
- 取消重复任务；
- 对失败任务降级或替换 Runtime；
- 发起定向辩论。

所有交互通过结构化 AgentMessage 记录，不直接拼接进全局 transcript。

---

## 11. Context Engineering

### 11.1 四层上下文

#### A. Canonical Decision State

长期保留、所有 Agent 不能直接覆盖：

- 决策问题；
- 方案集合；
- 用户确认事实；
- 用户偏好与约束；
- 阻断性未知；
- 计划版本；
- 当前阶段；
- 会议预算。

#### B. Evidence State

- EvidenceRecord；
- Claim-Evidence 映射；
- 来源质量；
- 发布时间和抓取时间；
- 冲突来源；
- 审计状态。

#### C. Agent Working Context

仅供某个 Agent 当前任务使用：

- ExpertTaskPacket；
- 相关用户事实；
- 相关证据；
- 当前工具观察；
- 当前任务的短期摘要。

#### D. Public Narrative

前端可展示内容：

- 当前阶段；
- Agent 正在执行的公开意图；
- 来源预览；
- 专家结论；
- 辩论中的公开分歧；
- 最终建议。

隐藏推理、内部 Prompt、敏感工具参数不得进入 Public Narrative。

### 11.2 Context Compiler

任何 Agent启动前，由 Context Compiler 从数据库状态编译任务上下文，而不是传递完整会议记录。

编译规则：

1. 只传与任务相关的方案和事实；
2. 用户确认事实优先于模型摘要；
3. 证据使用 ID 和短摘要，按需读取全文；
4. 第一轮不传其他专家立场；
5. 辩论只传争议 Claim、相关证据和对方公开结论；
6. 不传其他 Agent 的隐藏工作记录；
7. 每个 Context Packet 带 `context_version` 和内容摘要哈希；
8. 子 Agent 写回结构化 Delta，不能直接改 Canonical State。

### 11.3 Context Write-back

子 Agent完成后只写回：

- 专业结论；
- 使用的证据 ID；
- 新发现的假设和未知；
- 给用户或其他专家的问题；
- 立场和反转条件；
- 完成状态和预算用量。

主 Agent或确定性 Reducer 决定哪些 Delta 合并进 Canonical State。

### 11.4 压缩策略

压缩对象分开处理：

| 对象 | 策略 |
|---|---|
| 用户确认事实 | 永不通过模型自由改写，结构化保留 |
| 原始证据 | 保留原记录，Prompt 中只放摘要和 ID |
| Agent 工具观察 | 完成任务后压缩成 Observation Summary |
| 辩论记录 | 压缩成争议、双方依据、解决状态 |
| 主 Agent历史 | 保留 Plan 版本、Action、结果和状态变更 |
| 前端 transcript | 与模型上下文分离，不反向作为事实源 |

触发压缩：

- 单 Thread Token 达到窗口的 60%；
- 主 Agent完成一个 Workflow 阶段；
- 单个子 Agent完成任务；
- DebateIssue 关闭；
- 恢复长时间暂停的会议。

### 11.5 防止专家趋同

- 第一轮上下文隔离；
- 每个 ExpertTaskPacket 只包含其专业目标；
- Profile 中明确禁止跨专业推断；
- 工具权限不同；
- 输出验收标准不同；
- 辩论前不展示全体结论；
- 主 Agent按专业相关性和证据质量加权，不按措辞或多数票；
- 评测中增加角色可辨识度和重复率指标。

### 11.6 用户记忆

分为三类：

- `profile_memory`：稳定偏好，例如家庭责任、风险承受范围；
- `decision_episode`：过去一次决策、当时建议和后续反馈；
- `session_memory`：本次会议临时事实。

长期记忆进入任务前必须标注来源和时间；与用户当前表述冲突时，以当前表述为准并提示确认。

---

## 12. Evidence 与 Web Search

### 12.1 Evidence 状态

每条外部事实必须属于：

- `verified`
- `corroborated`
- `disputed`
- `unverified`
- `rejected`
- `stale`

`unverified/rejected/stale` 不得支撑最终报告核心结论。

### 12.2 来源策略

按问题领域定义来源路由：

1. 法律：法律法规、政府、法院、正式合同文本；
2. 金融与经济：监管机构、交易所、公司披露、官方统计；
3. 医疗与心理公共事实：权威医学机构、同行评议论文、专业协会；
4. 公司和行业：公司披露、监管文件、权威数据库、可靠媒体；
5. 历史案例：档案、学术出版物、权威机构；
6. 自媒体、问答社区和营销页只能作为线索，不能单独作为核心证据。

### 12.3 搜索 Loop

```text
Claim Decomposition
→ Source Plan
→ Candidate Search
→ Original Source Fetch
→ Authority/Freshness Check
→ Corroboration
→ Evidence Write
```

### 12.4 引用审计

最终报告中的每个关键 Claim 必须能够映射到：

```text
claim_id → evidence_id → source_url / user_fact / calculation
```

引用只显示真正使用的来源，不展示“网页来源”占位符，不把搜索摘要整段复制给用户。

---

## 13. Codex Harness 接入方案

### 13.1 以下游 Fork 为内核，扩展点优先

> 深墨维护一个固定版本的 Codex 下游 Fork，以 Codex Harness 作为 Agent Runtime 内核。
> 领域能力优先通过扩展点实现，必要的 Core Patch 单独记录，并建立 upstream 同步和回归测试机制。

- 内核能力（Thread/Turn/Item、Agent Loop、Tool Runtime、Compaction、Sandbox/Approval、
  Interrupt/Resume、事件流、错误恢复）由 Fork 提供，保持与上游一致；
- 深墨能力落点：`codex-rs/ext/decision-expert/`（ToolContributor/ContextContributor/
  TurnLifecycleContributor）+ skills/hooks/agent role + 产品仓库 Adapter；
- Python SDK 经 `CodexConfig.codex_bin + config_overrides` 指向自建二进制；
- 源码勘察证据与复用/扩展/改 Core 三分类见 `docs/codex-harness-inventory.md`；
- Fork 边界、同步、回滚、版本固定见 `docs/adr/ADR-codex-downstream-harness.md`。

### 13.1.1 下游 Fork 运营要点

- **仓库关系**：`upstream → openai/codex`（只读）；深墨 Fork 远端待建；
  产品仓库以 `backend/app/decision_harness/CODEX_FORK_PIN` 记录依赖的 fork commit
  （SHA + 上游 tag + 日期），与产品版本在同一提交中映射。
- **Core Patch 清单**：初始为空；确需改 core 时以 `patches/shenmo/*.patch` 维护并逐条
  记入 inventory §13，合并 upstream 时重放。
- **自定义 Runtime 构建部署**：`cargo build -p codex-app-server`（release 走
  `just build-for-release`）；产物随 worker 镜像分发，`codex_bin` 指向自建二进制；
  工具链钉死 `rust-toolchain.toml`（1.95.0）。
- **upstream 合并流程**：只合并 `rust-v*.*.*` release tag（不追 main HEAD）；
  冲突高发区预检（app-server-protocol/common.rs、protocol.rs、spec_plan.rs、ext/、MCP 系）；
  重放 patch 清单。
- **升级回归测试**：cargo check + app-server 握手冒烟 + 协议 schema 快照 diff
  （非新增类变更先升级产品侧 event_bridge/SDK 模型）+ `pytest tests/decision_harness/`
  + 端到端 Fake/真二进制双跑。

### 13.2 Runtime Adapter

定义统一接口，业务代码不得直接依赖 Codex SDK 类型：

```text
AgentRuntime
  start_session()
  resume_session()
  start_turn()
  interrupt_turn()
  compact_session()
  stream_events()
  close_session()
```

实现：

- `CodexAgentRuntime`：主路径；
- `LegacyGlmAgentRuntime`：兼容和降级；
- `FakeAgentRuntime`：单元测试；
- 后续可增加其他模型 Runtime。

### 13.3 Root 与 Child Thread

- 每场会议创建一个 Root Thread；
- 每个子 Agent任务创建独立 Child Thread 或隔离 Session；
- 父子关系记录在业务数据库；
- 子 Agent完成后只返回结构化结果；
- Root Thread 不直接吸收所有 Child 原始历史；
- 主 Agent需要补问时恢复对应 Child Thread；
- Debate 使用原 Child Thread 延续专业身份，但传入新的 Debate Packet。

### 13.4 工具暴露

Codex 默认的代码、Shell 和文件修改能力不属于决策产品能力。

必须：

- 关闭或拒绝不需要的文件写入和命令执行；
- 使用只读/最小权限 Sandbox；
- 只暴露 Domain Tool Gateway；
- 所有外部写操作默认不可用；
- 敏感数据读取需要权限策略；
- 工具调用保留 `tool_call_id`、参数摘要、结果状态和耗时。

首批工具：

```text
decision.get_state
decision.ask_user
decision.update_plan
agent.delegate_expert
agent.send_followup
research.search
research.fetch_source
evidence.read
evidence.write
evidence.audit
calculator.evaluate
memory.query
debate.open_issue
debate.respond
runtime.propose_finalize
```

### 13.5 App Server 生命周期

- App Server 由 Worker 进程管理，不由 HTTP 请求管理；
- 生产优先使用 Python SDK 管理其钉死版本的本地 Runtime；
- Runtime 崩溃后由 Adapter 重启并通过业务存储恢复 Thread 映射；
- WebSocket 不是第一阶段生产依赖；
- SSE 继续由现有 FastAPI 事件存储提供；
- Codex 事件先写数据库，再由 SSE 推送，避免浏览器断线影响 Agent。

### 13.6 模型策略

建议：

- 主 Agent 使用稳定的 Codex/OpenAI 模型；
- 子 Agent默认跟随主 Runtime；
- Legacy GLM 保留为降级或 Shadow 对照；
- Model Router 可以根据任务复杂度、延迟和成本选择模型；
- 不允许同一 Agent在未记录的情况下中途切换模型；
- 模型切换必须记录 `model`, `provider`, `reason`。

---

## 14. 状态与数据库设计

新增迁移建议：`backend/migrations/002_decision_agent_harness.sql`。

### 14.1 decision_runs

```text
id UUID PK
parliament_id UUID FK UNIQUE
runtime_mode TEXT
root_thread_id TEXT
workflow_phase TEXT
status TEXT
state_version INT
decision_state JSONB
active_plan JSONB
budget JSONB
usage JSONB
finalization_state JSONB
created_at / updated_at / finished_at
```

### 14.2 agent_runs

```text
id UUID PK
decision_run_id UUID FK
parent_agent_run_id UUID NULL
agent_type TEXT
expert_id TEXT NULL
runtime_provider TEXT
runtime_thread_id TEXT
task_id TEXT
status TEXT
input_context_version INT
input_context_hash TEXT
task_packet JSONB
result JSONB
budget JSONB
usage JSONB
error_code TEXT
retry_of UUID NULL
started_at / finished_at
```

### 14.3 plan_versions

```text
id UUID PK
decision_run_id UUID FK
version INT
plan JSONB
reason TEXT
created_by_agent_run_id UUID
created_at
UNIQUE(decision_run_id, version)
```

### 14.4 evidence_records

```text
id TEXT PK
decision_run_id UUID FK
claim_id TEXT
kind TEXT
status TEXT
source_title TEXT
source_url TEXT
source_domain TEXT
published_at TIMESTAMPTZ NULL
retrieved_at TIMESTAMPTZ
excerpt TEXT
relevance FLOAT
reliability FLOAT
authority_level TEXT
supports_claim TEXT
contradicts_claim TEXT
metadata JSONB
```

### 14.5 debate_issues

```text
id UUID PK
decision_run_id UUID FK
question TEXT
importance FLOAT
conflict_type TEXT
participants JSONB
claims JSONB
status TEXT
round_count INT
resolution JSONB
created_at / updated_at
```

### 14.6 事件表

继续使用 `parliament_events`，扩展事件类型，不另外建设一套前端流。

---

## 15. 事件协议与前端

### 15.1 新事件类型

```text
plan_created
plan_revised
user_input_requested
agent_task_created
agent_started
agent_action
agent_tool_started
agent_tool_completed
agent_partial
agent_completed
agent_failed
dispute_opened
debate_message
dispute_resolved
evidence_added
evidence_rejected
audit_started
audit_failed
audit_passed
finalization_proposed
partial_finalized
decision_finalized
runtime_recovered
```

### 15.2 前端展示原则

用户应看到：

- 主 Agent 当前计划；
- 为什么选择这些专家；
- 每位专家当前公开任务；
- 正在核实的事实；
- 哪个争议正在讨论；
- 哪些来源被接受或拒绝；
- 是否正在等待用户；
- 局部失败如何被降级处理。

用户不应看到：

- 隐藏推理；
- 内部系统 Prompt；
- 敏感工具参数；
- 大段原始搜索结果；
- 技术异常堆栈。

### 15.3 用户交互

现有 `/interrupt` 可兼容保留，但新 Runtime 中写入结构化 UserMessage，并立即通知主 Agent，而不是等待固定辩论节点。

新增或扩展：

```text
POST /api/parliament/{id}/messages
POST /api/parliament/{id}/cancel
POST /api/parliament/{id}/resume
GET  /api/parliament/{id}/runtime
```

旧接口保持兼容，避免前端一次性迁移。

---

## 16. 预算、并发与超时

### 16.1 分层预算

```text
Decision Budget
├── Main Agent Budget
├── Research Budget
├── Expert Task Budgets
├── Debate Budget
└── Audit/Synthesis Reserve
```

必须预留 Audit 和 Finalize 预算，不能让前面搜索耗尽全部资源。

### 16.2 并发策略

- 专家第一轮并行；
- 调研任务按来源域限制并发；
- 同一会议默认最多 3 个活跃 Child Agent；
- 全局继续使用 `llm_concurrency`；
- 主 Agent的用户交互和 Finalization 拥有保留槽位；
- Debate 只启动相关参与者，不再次运行全体专家。

### 16.3 超时策略

取消单一的“会议到点全部杀死”语义，改为：

- Tool timeout：工具降级；
- Turn timeout：中断当前 Turn，恢复 Thread 后缩小任务重试；
- Agent task timeout：返回 partial；
- Phase soft deadline：主 Agent评估收敛；
- Decision hard budget：停止新任务并 `PARTIAL_FINALIZED`；
- Runtime failure：重启 Runtime、恢复状态；
- 只有无法读取核心状态时才 `FAILED`。

---

## 17. 故障恢复与幂等

### 17.1 任务幂等

每个 Task 使用：

```text
idempotency_key = decision_run_id + plan_version + task_type + task_scope_hash
```

重复 Worker、重试或恢复不得生成重复专家发言和重复证据。

### 17.2 Checkpoint

检查点写入时机：

- 主 Agent Action 前后；
- Plan 创建或修改；
- 子 Agent任务创建和完成；
- 用户输入；
- DebateIssue 状态变化；
- Evidence Audit 完成；
- Finalization 状态变化。

### 17.3 局部失败矩阵

| 失败 | 处理 |
|---|---|
| 单次搜索失败 | 换查询或标记未知 |
| 来源打不开 | 使用其他来源，不引用该来源 |
| 单个专家超时 | 返回 partial，必要时缩小任务重试 |
| 单个专家结构错误 | 一次结构修复，再降级 |
| Debate 参与者失败 | 保留分歧并继续综合 |
| Evidence Audit 不通过 | 只修订相关 Claim |
| Root Turn 超时 | 恢复 Root Thread 并注入状态摘要 |
| App Server 崩溃 | Adapter 重启、恢复映射和待办任务 |
| SSE 断线 | Agent继续，前端按 seq 重连 |
| 用户取消 | 保存当前结果，停止创建新任务 |

---

## 18. 最终输出契约

```json
{
  "recommendation": {
    "option": "",
    "strength": "conditional",
    "summary": "",
    "applicable_conditions": []
  },
  "decision_model": {
    "options": [],
    "goals": [],
    "constraints": []
  },
  "option_comparison": [],
  "expert_findings": [],
  "resolved_disputes": [],
  "remaining_disagreements": [],
  "key_evidence": [],
  "remaining_uncertainties": [],
  "action_plan": [],
  "risk_controls": [],
  "reversal_conditions": [],
  "completion": {
    "status": "finalized",
    "limitations": []
  }
}
```

文案层可以重新组织，但结构化事实不能被文案生成器改写。

---

## 19. 现有代码迁移映射

### 19.1 保留

- `backend/app/agents/profiles.py`：八位专家定义；
- `backend/app/agents/evidence.py`：升级为共享 Evidence Kernel；
- `backend/app/agents/tool_registry.py`：升级为 Domain Tool Gateway；
- `backend/app/event_store.py`：继续作为前端事件源；
- `backend/app/worker.py`：继续承担后台运行，改为启动 Runtime Orchestrator；
- `backend/app/api/parliament.py`：保留 API 兼容；
- `backend/app/runtime_context.py`：迁移为持久化用户消息与取消信号；
- `backend/app/services/expert_selection.py`：作为主 Agent选择专家时的规则和兜底；
- `backend/app/playbooks/`：作为场景先验，不作为固定流程；
- 前端现有 SSE 断线重连逻辑。

### 19.2 新增建议目录

```text
backend/app/decision_harness/
  __init__.py
  schemas.py
  runtime.py
  codex_runtime.py
  legacy_runtime.py
  session_manager.py
  workflow.py
  supervisor.py
  scheduler.py
  context_compiler.py
  reducer.py
  policies.py
  budgets.py
  debate.py
  evidence_audit.py
  finalization.py
  event_bridge.py
  recovery.py
  model_router.py
```

### 19.3 旧节点映射

| 当前节点 | 新位置 |
|---|---|
| parse_topic | 主 Agent INTAKE + Decision Model |
| select_experts | PlanSpec + Scheduler Rule |
| clarification_gate | 主 Agent `ask_user` + Workflow Pause |
| parallel_research | Research Lead + Expert Child Tasks |
| opening_round | Expert Independent Result |
| extract_core_disputes | DebateIssue Extractor |
| cross_examination | Targeted Debate Task |
| convergence_round | 按争议收敛，不再固定第三轮 |
| chair_endorsement | 删除独立意义，并入主 Agent综合 |
| summary | Synthesis + Finalization Gate |
| archive | Decision Run Terminal Handler |

### 19.4 兼容开关

```text
DECISION_RUNTIME_MODE=legacy|shadow|codex_harness
```

- `legacy`：现有 LangGraph；
- `shadow`：用户看到旧结果，新 Runtime 后台运行并记录对比；
- `codex_harness`：新 Runtime 正式输出；
- 切换不得改变创建会议 API。

---

## 20. 分阶段实施计划

### Phase -1：Codex Harness 源码勘察

目标：在改动任何业务代码前，基于固定 commit 实读上游源码，固定 Fork 基线与扩展边界。

任务：

1. 克隆 openai/codex，记录并固定 commit SHA（勘察锚点 `343074d420`，2026-08-22；
   正式落地前改锚最近的 `rust-v*.*.*` release tag）；
2. 实读 Agent Loop、Thread/Turn/Item、Tool Runtime、Compaction、Subagent、
   App Server 协议、Approval/Sandbox、Interrupt/Resume、Provider 配置、事件链路；
3. 产出 `docs/codex-harness-inventory.md`（17 节）与
   `docs/adr/ADR-codex-downstream-harness.md`；
4. 验证构建链（rustup + 钉死工具链 + `cargo check` + 二进制握手冒烟）；
5. 输出 Core Patch 候选清单（初始为空）与推荐扩展目录。

验收：勘察文档齐备、构建验证通过、暂停等待确认后才进入正式编码。
（状态：已完成，2026-08-22。）

### Phase 0：契约与基线

目标：先固定接口和评测，不改变生产行为。

任务：

1. 增加 V4 schemas；
2. 建立当前 20—30 个真实决策案例基线；
3. 记录稳定性、完成率、引用正确率、重复率、延迟和成本；
4. 增加 Runtime feature flag；
5. 保证所有 legacy 测试继续通过。

验收：无用户行为变化，基线报告可重复生成。

### Phase 1：Runtime Adapter 与 Root Agent 垂直切片

任务：

1. 引入 Codex Python SDK；
2. 实现 `AgentRuntime` 和 `CodexAgentRuntime`；
3. 实现 Root Thread 创建、恢复、事件桥接和取消；
4. 实现 INTAKE、PlanSpec 和主 Agent Action Schema；
5. 只允许 Root Agent调用只读状态工具；
6. 运行到 Plan 结束，不执行专家。

验收：同一会议能跨 Worker 重启恢复 Plan；事件顺序可重放。

### Phase 2：Context Compiler 与一个专家子 Agent

任务：

1. 实现 ExpertTaskPacket；
2. 实现 Context Compiler；
3. 先接经济学家 Child Thread；
4. 实现结构化 Write-back；
5. 实现 partial 退出；
6. 验证 Root 不接收 Child 全量历史。

验收：经济学家能独立完成任务，失败不影响 Root 继续。

### Phase 3：八位专家与 Scheduler

任务：

1. 接入八位 Profile；
2. 按用户选择和主 Agent计划选择 3—5 位；
3. 实现并发、预算和公平调度；
4. 增加 Follow-up 和任务取消；
5. 增加角色越界与相关性检查。

验收：角色输出可辨识、低相关角色可以不发言、并行失败可隔离。

### Phase 4：Research Lead 与 Evidence Kernel

任务：

1. 重构搜索为 Claim 驱动；
2. 增加原始来源抓取；
3. 增加来源权威性和时效策略；
4. 建立共享 EvidenceRecord；
5. 禁止搜索摘要直接进入公开回复；
6. 增加 Claim-Evidence 映射。

验收：核心引用可打开、可对应、不过期；低质量来源不会成为唯一依据。

### Phase 5：Debate Loop

任务：

1. 实现 DisputeIssue Extractor；
2. 实现定向 Debate Packet；
3. 实现立场前后变化结构；
4. 实现两轮上限和无新增信息停止；
5. 实现用户事实注入后局部重开争议。

验收：辩论只围绕实质分歧，重复率显著下降。

### Phase 6：Evidence Audit 与 Finalization Gate

任务：

1. 实现 Evidence Auditor；
2. 实现定向修订；
3. 实现主 Agent退出条件；
4. 实现 `FINALIZED/PARTIAL_FINALIZED/WAITING_USER/CANCELLED/FAILED`；
5. 移除预算耗尽直接 failed 的路径。

验收：单 Agent超时不会导致整场失败；最终报告能说明限制和反转条件。

### Phase 7：前端体验

任务：

1. 展示主 Agent计划；
2. 展示专家任务和状态；
3. 展示争议卡片；
4. 展示来源接受/拒绝；
5. 支持等待用户和恢复；
6. 将技术失败文案替换为可理解的降级状态。

验收：用户能理解“系统现在为什么做这一步”。

### Phase 8：Shadow、灰度与切换

任务：

1. Shadow 跑真实匿名案例；
2. 自动对比 legacy 与 codex_harness；
3. 人工盲评最终建议；
4. 小比例灰度；
5. 达到门槛后扩大流量；
6. 保留一键回滚 legacy。

---

## 21. 测试与评测

### 21.1 单元测试

- Action Schema 拒绝非法动作；
- Context Compiler 不泄露其他专家第一轮结论；
- Reducer 不能覆盖用户确认事实；
- Budget 正确预留审计和总结资源；
- Finalization Gate 正确拒绝过早结束；
- Evidence Audit 拒绝无 URL 的网页证据；
- 子 Agent partial 能被主 Agent接收；
- 幂等键阻止重复任务。

### 21.2 集成测试

- Root 创建、恢复、压缩；
- 主 Agent规划后委派三个专家；
- 子 Agent并行完成；
- 一个子 Agent超时；
- 用户在 Debate 中补充事实；
- Audit 失败后只修订一个 Claim；
- Worker 重启后恢复；
- SSE 断线后按 seq 重放；
- 用户取消后不再创建任务；
- 硬预算耗尽后 `PARTIAL_FINALIZED`。

### 21.3 Evals

核心指标：

| 指标 | 定义 |
|---|---|
| Completion Rate | 非系统性失败完成率 |
| Recommendation Relevance | 是否直接回答用户问题 |
| Expert Distinctness | 角色视角可辨识度 |
| Repetition Rate | 专家和辩论重复程度 |
| Citation Entailment | 引用是否支持 Claim |
| Source Authority | 核心来源权威性 |
| Freshness | 时间敏感信息是否新鲜 |
| Unknown Honesty | 是否诚实标记未知 |
| Actionability | 是否给出行动顺序和触发条件 |
| Recovery Rate | 局部失败后成功完成比例 |
| User Interrupt Latency | 用户补充到主 Agent处理的延迟 |
| Cost / Latency | 每场会议成本和耗时分布 |

发布门槛建议：

- 系统性失败率低于 legacy；
- 引用支持率不低于 95%；
- 核心来源中低质量自媒体不作为唯一依据；
- 单 Agent失败时整场完成率不低于 95%；
- 最终建议盲评显著优于 legacy；
- P95 时延和单场成本在产品可接受范围内。

---

## 22. 可观测性

每场会议必须能按以下层级回放：

```text
Decision Run
├── Workflow Phase
├── Plan Version
├── Main Agent Action
├── Agent Task
│   ├── Runtime Turn
│   ├── Tool Call
│   └── Result
├── Debate Issue
├── Evidence Audit
└── Finalization Decision
```

必须记录：

- run_id / thread_id / turn_id / task_id；
- model/provider；
- 输入 Context 版本和哈希；
- 状态迁移；
- Token、耗时和工具次数；
- 重试和降级原因；
- Finalization Gate 拒绝或通过原因。

不得记录：

- 用户敏感信息明文副本；
- 模型隐藏思维过程；
- API Key；
- 未脱敏的工具参数。

---

## 23. 安全与权限

- 默认只读；
- 专家按 Profile 获得工具白名单；
- 法学家只能提供信息性分析，不冒充执业律师；
- 心理学家不得进行医学诊断；
- 金融建议明确风险与不确定性；
- 访问长期记忆时遵循最小披露；
- 搜索查询进入外部服务前脱敏；
- 外部写操作未来启用时必须逐项审批；
- 用户取消后所有新 Tool Call 被拒绝；
- Prompt Injection 内容作为不可信网页数据处理，不得改变 Agent 指令。

---

## 24. ZCode 实施要求

1. 先提交 Schema、Runtime 接口和测试，再接真实 Codex Runtime；
2. 不做大爆炸式重写；
3. 每个 Phase 单独提交，提交信息标明阶段；
4. 默认保持 `DECISION_RUNTIME_MODE=legacy`；
5. 新功能必须可通过 Fake Runtime 离线测试；
6. 不修改未纳入任务的用户本地改动；当前 `backend/app/services/decision_path.py` 有未提交改动，必须保留；
7. 不删除现有测试，只能更新明确因 V4 契约变化而失效的断言；
8. 数据库迁移必须向前兼容且可重复执行；
9. 前端不能依赖 Codex 事件格式，必须依赖产品自己的事件协议；
10. 所有异常先转为领域错误码，再生成用户文案；
11. 不在日志或事件中保存模型隐藏推理；
12. 每个 Phase 完成后运行后端、前端和 eval 测试，并记录结果。

推荐提交顺序：

```text
feat(runtime): add decision harness schemas and runtime protocol
feat(runtime): add codex app-server adapter and event bridge
feat(agent): add root planning loop and workflow gates
feat(agent): add scoped child-agent scheduler and context compiler
feat(evidence): add claim-based research and evidence audit
feat(debate): add dispute-driven multi-agent debate loop
feat(runtime): add finalization gate and partial completion
feat(ui): expose plans tasks disputes and recovery states
test(evals): add multi-agent quality and recovery evaluation suite
```

---

## 25. 完成定义

V4 只有在以下条件全部满足时才算完成：

- 主 Agent能够生成、执行和更新 Plan；
- 主 Agent能选择并委派 3—5 位专家；
- 子 Agent拥有独立 Loop 和上下文；
- 专家第一轮分析互相隔离；
- 主 Agent能提取并主持定向辩论；
- 主 Agent能在过程中与用户和子 Agent交互；
- 搜索使用原始、可核验、带时效信息的来源；
- 最终 Claim 能映射到 Evidence；
- 单 Agent失败不会终止会议；
- 预算耗尽产生阶段性报告而不是失败；
- Worker、Runtime 或 SSE 中断后能够恢复；
- Finalization Gate 能阻止主 Agent过早结束；
- 前端能够解释当前计划、进度、争议和降级状态；
- Shadow Eval 结果达到发布门槛；
- Legacy 一键回滚仍然有效。

---

## 26. 官方参考

- [Codex as a platform: build on the open agent harness](https://learn.chatgpt.com/blog/codex-as-a-platform)
- [Codex App Server](https://learn.chatgpt.com/docs/app-server)
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)

---

## 27. 最终架构判断

这次升级不是把 LangGraph 换成另一个框架，也不是把八位专家改成八个独立聊天机器人。

最终产品形态应当是：

> Codex Harness 提供可持续运行的 Agent Runtime；深墨决策台提供决策领域的计划、上下文、证据、专家协作、辩论规则和退出标准；外层 Workflow 保证整个多 Agent 系统稳定、可解释、可恢复。

这既保留 LLM 的自主规划与工具使用能力，也保留产品必须拥有的确定性和用户控制权。
