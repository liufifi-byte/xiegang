# Codex Harness 源码勘察（Phase -1）

> 状态：已完成（2026-08-22）。本文档所有结论均来自对固定 commit 的实际源码阅读，
> 引用格式 `文件路径:行号`，路径相对于仓库根 `codex-fork/`（即上游 `openai/codex`）。
> 配套决策记录见 `docs/adr/ADR-codex-downstream-harness.md`。

## 1. 上游仓库地址和 commit SHA

- 上游：https://github.com/openai/codex
- 本地克隆：`/Users/xinyiliu/Desktop/codex-fork`（blobless partial clone，工作树完整）
- **固定 commit：`343074d4207d572809bd8cea15f4be1d09d98e0b`**
  （`343074d420`，2026-08-22，"Report runtime MCP connection status (#40068)"）
- remote 配置：`upstream → https://github.com/openai/codex`；`origin` 未设置
  （深墨 Fork 远程仓库待确认后配置，见 ADR §6）
- 同期版本锚点：Python SDK `openai-codex==0.147.0` 钉的是 `openai-codex-cli-bin==0.147.0`
  （`sdk/python/pyproject.toml:19`）；上游主线 tag 已到 `rust-v0.150.0-alpha.N` 系列。
  **本勘察锁定的是 main 分支 HEAD，比 0.147.0 SDK 新约 3 个 minor。**

## 2. Codex Rust workspace 结构

- Rust workspace 在 `codex-rs/`，**135 个成员 crate**（`codex-rs/Cargo.toml` members），
  全 workspace 约 3274 个 `.rs` 源文件。crate 一律 `codex-` 前缀（根 `AGENTS.md` 约定）。
- 关键 crate 分组：
  - **内核**：`core`（会话/循环/上下文，440 个 src 文件）、`protocol`（wire 协议单一权威）、
    `tools`（工具抽象）、`sandboxing`（三平台沙箱）、`execpolicy`（Starlark 规则）。
  - **App Server**：`app-server`（二进制）、`app-server-protocol`（方法/通知定义，不依赖 core）、
    `app-server-transport`（stdio/UDS/WebSocket）。
  - **持久化**：`rollout`（JSONL 会话记录）、`thread-store`（ThreadStore trait + 本地实现 + 跨进程锁）、
    `history`（RolloutItem 模型）、`state`（SQLite 索引，6 个库）。
  - **多 Agent**：`agent-graph-store`、`agent-identity`、`core/src/agent/`、`core/src/codex_delegate.rs`。
  - **扩展体系**：`ext/extension-api`（13 类 Contributor 的进程内插件 API）、
    `ext/web-search`、`ext/goal`、`ext/image-generation`（范例扩展）、
    `features`（120 个 feature flag）、`skills`、`hooks`、`plugin`/`core-plugins`（用户态插件）、
    `codex-mcp`/`rmcp-client`/`mcp-server`（MCP 三向）。
  - **Provider**：`model-provider`、`model-provider-info`、`codex-api`、`config`。
  - **SDK**：仓库根 `sdk/python`、`sdk/python-runtime`（`openai-codex-cli-bin` 的源码）、`sdk/typescript`。
- 构建：Bazel（`MODULE.bazel`、`just bazel-codex`）与 cargo 并存；release 走 Bazel
  （`just build-for-release` → `//codex-rs/cli:release_binaries`），日常开发 cargo。
- `[patch.crates-io]`：crossterm/tungstenite 指向 openai-oss-forks 的 git rev
  （`codex-rs/Cargo.toml:575-587`）——fork 环境需能访问 GitHub git 仓库。

## 3. Agent Loop 入口和调用时序

两层循环，都在 `codex-core`：

1. **提交循环**：`core/src/session/handlers.rs:515` `submission_loop`，
   `while let Ok(sub) = rx_sub.recv()` 分发 `Op::*`（TurnInput/Interrupt/Compact/
   InterAgentCommunication/Review…），Session spawn 时 `tokio::spawn` 挂起
   （`core/src/session/mod.rs:785`）。
2. **采样循环（真 Agent Loop）**：`core/src/session/turn.rs:153` `run_turn`。
   由 `SessionTask` trait（`core/src/tasks/mod.rs:187`）的 `RegularTask::run`
   （`core/src/tasks/regular.rs:39-91`）驱动：先发 `EventMsg::TurnStarted`，然后
   `loop { run_turn(); if !has_pending_input { return } }`。

`run_turn` 每轮（turn.rs:301-586）：取 pending input（steer/邮件）→ 跑 hooks 并记历史 →
`capture_step_context`（请求级快照，`session/mod.rs:3160`）→ `clone_history().for_prompt()`
（上下文拼装，`context_manager/history.rs:206`，先经 `normalize_history` 保证 call/output 配对）
→ `build_prompt`（`turn.rs:1312`，含 tools/base_instructions/output_schema）→
`run_sampling_request`（重试循环 turn.rs:1368 → `try_run_sampling_request` turn.rs:2179）
→ 按返回决定 continue / auto-compact / break。

**完整调用链**（从 app-server 客户端输入到 Turn 完成）：

```text
[客户端] turn/start (JSON-RPC)
 └─ CodexThread::start_or_steer_turn          core/src/codex_thread.rs:338
     └─ SessionIo::submit_turn_input (Op::TurnInput)  session/mod.rs:844
         └─ submission_loop 分发               session/handlers.rs:570-577
             └─ spawn_task → RegularTask::run  tasks/mod.rs:279/382; tasks/regular.rs:39
                 ├─ EventMsg::TurnStarted
                 └─ run_turn                   session/turn.rs:153
                     ┌─ 每轮循环 ────────────────────────────────┐
                     │ hooks + 记录输入        turn.rs:305-323   │
                     │ clone_history→for_prompt 上下文编译        │
                     │ build_prompt           turn.rs:1312       │
                     │ 模型流式调用            client.rs:1883     │
                     │   （仅 Responses API；优先 WS，回落 HTTP   │
                     │     POST /responses，client.rs:162/1603） │
                     │ OutputItemDone→工具调用  stream_events_utils.rs:289 │
                     │   → ToolRouter→Registry dispatch          │
                     │   → 结果 FunctionCallOutput 回填历史       │
                     │     session/mod.rs:3062→3094→3107         │
                     │ needs_follow_up? 是→continue 否→break     │
                     │   （turn.rs:423/500-550）                 │
                     └────────────────────────────────────────────┘
                 └─ on_task_finished           tasks/mod.rs:571
                     ├─ flush_rollout（持久化落盘）
                     ├─ EventMsg::TurnComplete / TurnAborted
                     └─ 有排队输入则续开新 turn
```

**循环退出原因**（turn.rs + tasks/mod.rs）：
- final answer：`!needs_follow_up` → break（stop hooks 可强制 continue）；
- 工具调用产生 follow-up：`needs_follow_up=true`（stream_events_utils.rs:326）；
- 中断：`Op::Interrupt` → `abort_all_tasks(Interrupted)`（tasks/mod.rs:494），先写 rollout 再发 TurnAborted；
- token/预算：`ContextWindowExceeded`/`UsageLimitReached`（turn.rs:1405-1415）；
- **没有显式 max-turns 计数器**——防线是 token 限额 + auto-compact
  （turn.rs:469 注释明言依赖 compaction 防死循环）。

**Tool Call 如何进入下一轮上下文**：工具 future 产出 `ResponseInputItem`（失败/取消也有对应
输出，`tools/parallel.rs:216-252`），经 `record_conversation_items` 写进
`ContextManager` 历史（`session/mod.rs:3062→3094`）并同时持久化 rollout，下一轮
`for_prompt()` 自然带上。**Thread 如何持久化**：见 §8。

## 4. Thread / Turn / Item 数据模型

- **Thread**：`CodexThread`（`core/src/codex_thread.rs:202`，`Arc<Session>` + SessionIo +
  rollout_path），管理器 `ThreadManager`（`thread_manager.rs:218`，
  `RwLock<HashMap<ThreadId, Arc<CodexThread>>>`）。thread_id 贯穿 rollout 文件名与 SQLite 索引。
- **Turn**：`TurnContext`（turn 级配置，`session/turn_context.rs:144`）；运行态
  `ActiveTurn`/`RunningTask`/`TurnState`（`state/turn.rs:32/74/89`，含 cancellation_token、
  pending approvals、in-flight tool calls）；`TaskKind` = Regular/Review/Compact
  （`state/turn.rs:68`）。`TurnStatus` = Completed/Interrupted/Failed/InProgress
  （`protocol/src/protocol.rs` 与 app-server `v2/turn.rs:30-35`）。
- **Item**：客户端视角 `TurnItem` enum 18 个变体（`protocol/src/items.rs:44-75`：
  UserMessage/AgentMessage/Plan/Reasoning/CommandExecution/McpToolCall/WebSearch/
  SubAgentActivity/ContextCompaction…）；模型侧 wire `ResponseItem`（`protocol/src/models.rs:940`）。
- **状态机**：`AgentStatus`（PendingInit→Running→Interrupted/Completed/Errored/…）由事件推导
  （`core/src/agent/status.rs:6-21`）；`TurnAbortReason` = Interrupted/Replaced/ReviewEnded/BudgetLimited
  （`protocol.rs:3997-4002`）；`MailboxDeliveryPhase`（CurrentTurn↔NextTurn，`state/turn.rs:48-56`）。

## 5. Tool Runtime

- **抽象**：`ToolExecutor<Invocation>` trait（`tools/src/tool_executor.rs:106`：
  tool_name/spec/exposure/handle）+ core 内部 `CoreToolRuntime`（`pub(crate)`，
  `core/src/tools/registry.rs:55`）+ 输出契约 `ToolOutput`（`to_response_item()` 转回
  FunctionCallOutput）。
- **注册**：`ToolRegistry`（`registry.rs:271`）；每回合 `build_tool_router`
  （`core/src/tools/spec_plan.rs:117`）按序注入：core 内置 → MCP 工具
  （`mcp_tool_exposure.rs:37`，命名 `server__tool`）→ **扩展工具
  （`append_extension_tool_executors`，spec_plan.rs:1258）** → 动态工具（客户端声明，
  spec_plan.rs:1206）。
- **执行管线**：PreToolUse hooks（可 Block/改参）→ `tool.handle()` → PostToolUse hooks
  （可改模型可见输出）→ `AnyToolResult`（`registry.rs:479-753`）。shell 类工具内部走
  `ToolOrchestrator`：① 审批 → ② 选沙箱 → ③ 沙箱内执行 → ④ 拒绝时按策略重审/升级
  （`core/src/tools/orchestrator.rs:125`）。
- **内置工具清单**（注册点 spec_plan.rs）：`exec_command`/`write_stdin`/`apply_patch`/
  `view_image`/`plan`/`update_plan`/`request_user_input`/`send_user_message_async`/
  `request_permissions`/`new_context_window`/`get_context_remaining`/`current_time`/`sleep`/
  `tool_search`/多代理 v1（spawn_agent/send_input/resume_agent/wait_agent/close_agent）/
  多代理 v2（spawn_agent/send_message/followup_task/wait_agent/interrupt_agent/list_agents）/
  MCP 资源读取三件套/`web.run`（ext/web-search）/`image_gen.imagegen`。
- **新增自定义工具的最小侵入路径**（无需改 core）：实现
  `ToolContributor`+`ToolExecutor<ToolCall>`（`ext/extension-api/src/contributors.rs:288`），
  在 app-server `thread_extensions` 安装（`app-server/src/extensions.rs:55`）。
  范例：`ext/web-search/src/tool.rs:52`、`ext/goal`。

## 6. Context 与 Compaction

- 上下文真源：`ContextManager`（`core/src/context_manager/history.rs:45`，
  items + history_version + token_info）；每轮 `for_prompt()` 产出模型输入。
- **触发**：默认上下文窗口 90%（`protocol/src/openai_models.rs:486-496`）；
  判定 `session/context_window.rs:23-91`；pre-turn（turn.rs:1011）与 mid-turn（turn.rs:411-498）
  两个触发点；用户可 `/compact` 手动（`Op::Compact`）。
- **三条压缩路径**（分派 `tasks/compact.rs:36-78`）：
  1. TokenBudget 开新窗（实验，默认关）；
  2. **远程压缩（仅 OpenAI provider**，`model-provider/src/provider.rs:341`：
     `is_openai()` 才支持，第三方一律 Unsupported）；
  3. **本地压缩（第三方 provider 的路径）**：用 `prompts/templates/compact/prompt.md`
     把全史交给模型重总结（`core/src/compact.rs:240-345`），保留最近用户消息（≤20k token）+ 摘要。
  **深墨若用非 OpenAI provider，自动走本地压缩。**
- **旧上下文可找回**：rollout JSONL 只追加；压缩写一条 `RolloutItem::Compacted`
  （含 replacement_history，`history/src/lib.rs:141-150`），旧 item 仍在文件里；
  resume/fork 反向扫描重建（`core/src/session/rollout_reconstruction.rs:140-190`）。

## 7. Subagent 机制

- **子 Agent 不复用父 Thread**：`spawn_agent` 工具（`core/src/tools/handlers/multi_agents/spawn.rs:44`）
  → `AgentControl::spawn_agent_internal`（`core/src/agent/control/spawn.rs:403`）新建独立
  Thread/Session（`parent_thread_id` + `ThreadSource::Subagent`），共享同一 `AgentControl`
  （执行限额、rollout 预算）与 thread_store。深度上限与 spawn slot 有检查（spawn.rs:420-441）。
- fork 模式可从父 rollout 加载模型上下文建子线程（`spawn_forked_thread`，spawn.rs:609）。
- **通信**：不走共享内存——`InterAgentCommunication`（protocol.rs:744）经
  `AgentControl::send_inter_agent_communication`（control.rs:211）投到目标线程的
  submission 通道，进 mailbox 队列（handlers.rs:82-102）。子线程终态经
  `maybe_notify_parent_of_terminal_turn` 回传父线程（session/mod.rs:1977）。
- **权限继承**：子代理继承父会话 approval policy 与 permission profile 快照
  （spawn.rs:307-341）；更严的子代理要靠 **agent role**（`agent_roles[].config_file`，
  `core/src/agent/role.rs:51-129`，白名单里明确可关 ShellTool 等 feature）。

## 8. App Server 协议

- 二进制 `codex-app-server`（`app-server/src/main.rs:65`），默认 stdio（JSONL），
  支持 `unix://`、`ws://`（`app-server-transport/src/transport/mod.rs:74`）。
  JSON-RPC 为**自研变体**（无 `jsonrpc` 域，`app-server-protocol/src/rpc.rs:1`）。
- **方法清单**：单一事实源 `app-server-protocol/src/protocol/common.rs` 的三个宏
  （client_request_definitions :487-1393 / server_request_definitions :1663-1734 /
  server_notification_definitions :1818-1933）。核心方法：
  `initialize`、`thread/start|resume|fork|archive|list|read|inject_items|rollback`、
  `turn/start|steer|interrupt`、`account/*`、`config/*`、`model/list`、审批反向请求
  （`item/commandExecution/requestApproval` 等 10 个 server→client 请求）。
  大量方法标 `#[experimental]`（queue/realtime/remoteControl/process/* 等）——
  未开 `experimentalApi` capability 的连接会被拒绝或剥字段。
- **通知全集**：`turn/started|completed`、`item/started|completed`、`item/agentMessage/delta`、
  `thread/tokenUsage/updated`、`thread/status/changed`、error（含 willRetry）、审批请求等
  70+ 个。Python SDK 的 `ItemCompletedNotification`/`TurnCompletedNotification` 与之对应
  （payload 在 `protocol/v2/*.rs`）。
- **app-server ↔ core 边界**：进程内直接调用（`MessageProcessor::new` 构造
  `codex_core::ThreadManager`，`app-server/src/message_processor.rs:291-364`），core 是内嵌
  actor；每线程一个 listener 消费 `CodexThread::next_event()`（thread_lifecycle.rs:307），
  `EventMsg` → `ServerNotification` 的翻译在 `bespoke_event_handling.rs`，只发给订阅连接。
- **多客户端并发**：订阅共享非独占（多个连接可订阅同一线程）；进程内 spawn 幂等保护
  （`thread_manager.rs:1965-1989`）；**跨进程**靠 per-thread 文件写锁
  （`$CODEX_HOME/thread-writer-locks/<id>.lock`，`thread-store/src/local/writer_lock.rs:17`，
  冲突报 `ThreadStoreError::Conflict`）。
- **版本兼容**：**无协议版本协商**——握手只换 clientInfo + capabilities
  （experimentalApi / optOutNotificationMethods 等）；兼容靠 deprecated 保留 +
  `deprecationNotice` 通知 + 按二进制版本导出 schema
  （`codex app-server generate-json-schema`）。

## 9. Approval、Sandbox 和权限

- **SandboxPolicy**（用户级，protocol.rs:1010-1058）：danger-full-access / read-only（带
  network_access 布尔）/ workspace-write / external-sandbox。运行时权威
  **PermissionProfile**（models.rs:411），内置 `:read-only`/`:workspace`/`:danger-full-access`。
- **平台实现**（`sandboxing` crate）：macOS Seatbelt（`/usr/bin/sandbox-exec` 固定路径 +
  SBPL 模板，seatbelt.rs:56）；Linux bubblewrap+seccomp（`linux-sandbox` helper 两阶段执行）；
  Windows RestrictedToken。
- **网络**：`network-proxy` crate（本地 HTTP+SOCKS5 强制代理、域名 allowlist、MITM CA）；
  沙箱网络策略只放行代理端口，被拒触发网络审批。
- **Approval**：`AskForApproval` = untrusted/on-request（默认）/granular/never
  （protocol.rs:924-947）；决策优先级 hooks → Guardian 自动审查 → 用户审批
  （`core/src/tools/approvals.rs:454-474`）；用户审批走 `EventMsg::ExecApprovalRequest`
  事件 + oneshot（session/mod.rs:2390），由 CLI/app-server 客户端批准。
- **execpolicy**：**Starlark** 语法 prefix 规则（非 CEL），可 forbidden/prompt/allow 硬控 shell。
- **决策专家「只读+搜索、禁 shell/写文件」可直接组合**：`:read-only` profile（或
  `ReadOnly{network_access:true}`）+ `Feature::ShellTool` 关闭（`spec_plan.rs:962-971`
  直接不注册 exec_command）+ agent role 强制（role.rs:91-106 白名单）+ `web.run` 搜索工具。

## 10. Interrupt、Resume 和错误恢复

- **interrupt**：`turn/interrupt` → `Op::Interrupt` → `abort_all_tasks(Interrupted)` →
  取消 CancellationToken（100ms 优雅期）→ **先写 rollout 再发 TurnAborted**
  （tasks/mod.rs:919-935，注释：客户端收到 abort 会同步重读 rollout）。
  turn/interrupt 的 JSON-RPC 响应延迟到 TurnAborted 事件到达才回。
  另有 `Op::RecoverTurn` 可恢复被中断的常规 turn。
- **resume**：`thread/resume` → 进程内已加载则 rejoin；否则从 rollout JSONL（+SQLite 索引）
  读 `StoredThread` → `InitialHistory::Resumed` 重建 Session
  （`app-server/src/request_processors/thread_processor.rs:3524-3598`），并恢复持久化的
  approval/permission 设置。损坏的 SQLite 会被备份重建（lib.rs:1228-1299）。
- **错误恢复**：JSON-RPC 错误码 -32600/-32601/-32602/-32603/-32001（overloaded，客户端应
  指数退避重试）；请求按 serialization scope 排队（同线程 turn/start 与 interrupt 严格按序）；
  **transport 断连不杀线程**——idle 30 分钟才卸载（thread_lifecycle.rs:7），期间 resume 可
  rejoin；stdio 单连接断开 = server 退出（stdio 模式生命周期即客户端生命周期）。
  流级错误：中间态 error 带 `willRetry:true` 不打断 turn。
- **过载**：入口通道（容量 128）满时请求直接 -32001；出站慢连接队列满则断开该连接。

## 11. 可直接复用的组件

| 组件 | 用法（零改动） |
|---|---|
| `:read-only` permission profile + 三平台沙箱 | 专家 Agent 安全基座，配置即用 |
| rollout JSONL 持久化 + thread/resume | Worker 重启恢复（我们 Phase 1 的 root_thread_id 恢复正对应它） |
| `turn/start|steer|interrupt` + 70+ 通知 | 主/子 Agent Loop 驱动与事件流 |
| `web.run` / hosted web_search / MCP | 调研与证据工具 |
| AskForApproval + execpolicy | 权限兜底（Starlark forbidden 硬拒 shell） |
| agent role（`agent_roles[].config_file`） | 每位专家一个 role：专属 config + 强制关 shell |
| spawn_agent 多代理 v1/v2 + AgentControl | Scheduler 底座（并发上限、深度上限、预算） |
| network-proxy 域名白名单 | 搜索域收口 |
| Dynamic Tools（客户端执行） | decision.ask_user / evidence.write 等业务工具可先走这条路 |
| Python SDK `CodexConfig.codex_bin + config_overrides` | 直接指向自建二进制，SDK 零改动 |

## 12. 可通过扩展实现的部分（不动 core）

- **进程内扩展**（推荐落点）：`ext/extension-api` 的 13 类 Contributor
  （Tool/Context/ThreadLifecycle/TurnLifecycle/ApprovalReview/…，`registry.rs:21-47`），
  照 `ext/goal`、`ext/web-search` 模板新建 `ext/decision-expert` crate。
- skills（SKILL.md 提示词包）、hooks（11 事件，压缩前后/工具前后拦截）、
  plugin/core-plugins（打包分发）。
- MCP server 对接（外部工具进程）与 mcp-server 反向暴露。
- 自定义 model provider 指向自家 Responses 端点（config `model_providers`，用户自定义只增不覆盖内置）。
- features crate 加深墨自己的 feature flag（规范位置）。

## 13. 必须修改 Core 的部分（Core Patch 候选清单）

按当前需求评估，**预计极少**，且每条都要在 ADR 记录并保持最小 diff：

1. **严格工具白名单**：目前无单一「允许工具清单」配置——feature flags + role + omit_tools_from
   只能近似；若要硬保证（审计级），需在 `spec_plan.rs`/`finalize_tool_router` 加过滤钩子（小改）。
2. **子代理独立 permission profile**：spawn.rs:307-341 强制继承父快照；若某专家需要与主席
   不同的 profile，需扩展 spawn 参数或 role 层（可先避免：让主席也用只读 profile）。
3. **绕过/自定义 ToolOrchestrator 审批-沙箱序列**：orchestrator 流程硬编码；业务工具走
   extension/Dynamic Tools 即可绕开，**无需改**。
4. 自带模型端点若只支持 Chat Completions：**wire_api=chat 已被上游移除**
   （`model-provider-info/src/lib.rs:57-91`），必须实现 Responses API 兼容层（我们侧网关做，
   不改 core）。
5. 协议扩展（新增决策类通知/方法）：改 `app-server-protocol` 宏定义 + 重生成 Python/TS SDK
   模型（`just write-app-server-schema`）——属于协议面变更，需走版本快照流程。

## 14. 深墨业务层应该保留的部分

（= 产品仓库职责，与 spec §4.1 一致）Decision Model / PlanSpec / 八专家 Profile /
ExpertTaskPacket / Evidence Ledger / DebateIssue / Finalization Gate / 最终报告 /
前端事件协议（§15.1 产品事件）/ PostgreSQL 业务状态（decision_runs 等 5 表 + parliament_events）/
legacy|shadow|codex_harness 切换。**Codex Thread 不是唯一业务事实源**——它是 Agent Runtime
状态，业务事实在我们的库里；root_thread_id 只是我们引用它的句柄
（已落地：`backend/app/decision_harness/repository.py` 的 `root_thread_id` 字段）。

## 15. 上游同步时可能发生冲突的文件

- **高频改动区**（近 15 commit 集中地）：`app-server-protocol/src/protocol/common.rs`
  （方法宏）、`protocol/src/protocol.rs`（EventMsg/Op）、`core/src/tools/spec_plan.rs`、
  `ext/guardian-v2/`、MCP 系 crate、hooks。
- **活跃度**：2026-08-01 至 08-22 约 880 个 commit；主线 tag 到 0.150.0-alpha，
  **追 HEAD 的 fork 维护成本高**，必须锚定 release tag 而非 main（见 ADR §5）。
- 42/120 feature 处于 UnderDevelopment（code-mode、guardian-v2、TokenBudget 压缩等），
  依赖这些区域的扩展会持续承受 churn。
- `[patch.crates-io]` 的 openai-oss-forks git 依赖 + Bazel lockfile 双构建系统是
  环境性风险点（需网络可达 GitHub；Bazel 变更要求 `just bazel-lock-update`）。

## 16. 推荐的改造目录和模块边界

```text
codex-fork/（深墨下游 Fork）
├── codex-rs/ext/decision-expert/        # 新增：深墨决策扩展 crate
│   ├── src/tools/                        #   decision.get_state / evidence.write / debate.* …
│   │                                     #   （ToolContributor + ToolExecutor<ToolCall>）
│   ├── src/context.rs                    #   ContextContributor：注入 ContextPacket
│   └── src/turn_lifecycle.rs             #   TurnLifecycleContributor：预算/静默钩子
├── codex-rs/app-server/src/extensions.rs # 一行安装（与 ext/web-search 同待遇）
├── codex-rs/features/src/lib.rs          # 深墨 feature flag（可选）
└── （不改 core；Core Patch 单独 branch + 补丁文件清单）
```

产品仓库 `xiegang/`：`backend/app/decision_harness/`（已建：schemas/runtime/codex_runtime/
repository/event_bridge/workflow + migration 002）继续作为 Adapter 与业务控制面；
Python SDK 侧用 `CodexConfig.codex_bin` 指向自建二进制（或发布自有
`openai-codex-cli-bin` 替身包，改 `sdk/python/pyproject.toml:19` 与
`sdk/python/_runtime_setup.py:27` 的 REPO_SLUG）。

## 17. 最小可运行验证方法

1. 工具链：rustup + `codex-rs/rust-toolchain.toml` 钉死 **1.95.0**（clippy/rustfmt/rust-src）。
2. **已验证（2026-08-22，本机 macOS arm64）**：
   - `cargo check -p codex-app-server` 通过（4m21s，1.95.0-aarch64-apple-darwin）；
   - `CARGO_PROFILE_DEV_DEBUG=0 cargo build -p codex-app-server` 通过（7m51s；
     注意开发机磁盘余量需 ≥15GB）；
   - stdio `initialize` 握手冒烟通过：返回 userAgent/codexHome/platform 字段
     （详见 ADR §11）。
3. 日常命令（`justfile`）：`just codex`（跑 CLI）、app-server 冒烟
   `cargo build -p codex-cli && cargo run -p codex-app-server-test-client -- --codex-bin ./target/debug/codex`；
   测试 `just test`（cargo nextest，全量约 1.4 万标注）；打包 `just assemble-codex-package`；
   release `just build-for-release`（Bazel）。
4. 每次同步 upstream 后的最小回归：`cargo check -p codex-app-server` →
   app-server-test-client 握手 → 我们 Python 侧
   `tests/decision_harness/`（50 个，含 CodexAgentRuntime fake-client 全套）。
