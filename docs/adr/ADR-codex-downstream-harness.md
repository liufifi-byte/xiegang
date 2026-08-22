# ADR：采用 Codex 下游 Fork 作为 Agent Runtime 内核

- 编号：ADR-codex-downstream-harness
- 状态：已接受（2026-08-22，Phase -1 勘察完成后）
- 关联：`docs/codex-harness-inventory.md`（源码勘察证据）、
  `docs/superpowers/specs/2026-08-22-codex-decision-multi-agent-harness-design.md`

## 1. 背景与决策

深墨决策台（邪杠会议室）要从 LangGraph 工作流升级为「确定性 Workflow 外壳 +
主/子 Agent Loop」架构。需要选一个 Agent Runtime 内核：Thread/Turn/Item、Agent Loop、
Tool Runtime、Context Compaction、Sandbox/Approval、事件流、Interrupt/Resume、错误恢复。

**决策：深墨维护一个固定版本的 openai/codex 下游 Fork，以 Codex Harness 作为
Agent Runtime 内核；领域能力优先通过扩展点实现；必要的 Core Patch 单独记录；
建立 upstream 同步和回归测试机制。**

基线 commit：`343074d4207d572809bd8cea15f4be1d09d98e0b`（2026-08-22 main HEAD，
勘察锚点）。正式落地前应改锚最近的 `rust-v*.*.*` release tag（见 §5）。

## 2. 为什么采用下游 Fork

1. **自研代价不可接受**：勘察显示内核含 135 个 crate 的 workspace、三平台沙箱
   （Seatbelt/bwrap+seccomp/RestrictedToken）、JSONL rollout 持久化 + SQLite 索引 +
   跨进程线程写锁、70+ 通知的 JSON-RPC 协议、多代理 spawn/通信/预算体系、三条压缩路径。
   这些正是 spec §13 要求的全部能力，自研等于重写一个 Codex。
2. **扩展点成熟度超预期**：`ext/extension-api` 提供 13 类 Contributor 的类型安全
   进程内插件 API（Tool/Context/TurnLifecycle/ApprovalReview…），有 `ext/goal`、
   `ext/web-search` 官方范例；加上 skills/hooks/MCP/Dynamic Tools/agent role，
   绝大多数深墨需求可以**零 core 改动**落地（inventory §11-12）。
3. **只读沙箱开箱即用**：`:read-only` permission profile + `Feature::ShellTool`
   开关 + agent role 强制，直接满足「专家禁 shell/写文件、只读+搜索」的红线。
4. **SDK 已兼容 Fork**：Python SDK 的 `CodexConfig.codex_bin + config_overrides`
   可直接指向自建二进制（`sdk/python/src/openai_codex/client.py:176-252`），
   我们的 `CodexAgentRuntime`（Phase 1 已提交）无需重写。

## 3. 为什么不完全重写 Agent Runtime

- 上游每周数百 commit 的迭代速度（8 月 1-22 日约 880 commit），自研分支会迅速
  失去沙箱安全修复、协议演进和压缩算法改进。
- 我们的产品差异点（决策建模、八专家、证据内核、辩论、Finalization Gate）全部
  在**业务语义层**，不在 Runtime 机制层——重写 Runtime 不产生产品价值。

## 4. 为什么不把全部业务逻辑塞进 Codex Core

- Core 里的 `SessionTask`/`run_turn`/`ToolRegistry` 全是 `pub(crate)`（inventory §3-4），
  业务塞进去 = 永久放弃 upstream 合并能力。
- 业务事实源必须在我们的 PostgreSQL（spec §14）：decision_runs/plan_versions/
  evidence_records 等；**Codex Thread 只是 Runtime 状态，不是业务事实源**。
- 前端只依赖产品事件（spec §15.1），不允许直接消费 Codex 原始事件——翻译层
  （event_bridge）必须在产品仓库里。

## 5. 模块边界（允许/禁止）

| 层 | 位置 | 政策 |
|---|---|---|
| 深墨扩展 | `codex-rs/ext/decision-expert/`（新增 crate） | ✅ 主要落点：ToolContributor/ContextContributor/TurnLifecycleContributor |
| 安装接线 | `app-server/src/extensions.rs` 一行 | ✅ 与 ext/web-search 同待遇 |
| feature flag | `features/src/lib.rs` 追加深墨项 | ✅ 允许 |
| 提示词/skill | skills、`collaboration-mode-templates` | ✅ 允许 |
| 协议扩展 | `app-server-protocol` 宏 + SDK 再生成 | ⚠️ 谨慎：须同步重生成 Python/TS 模型并出 schema 快照 |
| core 内核（session/tasks/tools/sandboxing） | `core/src/**` | ❌ 禁止承载业务；Patch 走独立分支 + 清单 |
| 持久化布局 | rollout/thread-store | ❌ 只读使用，不改格式 |
| 业务模型/DB/API/前端 | 产品仓库 `xiegang/` | ✅ 全部留在产品仓库 |

**Core Patch 清单**（初始为空；每次新增须记入 inventory §13 并在 fork 内以
`patches/shenmo/*.patch` 形式维护）：
1. （暂无。严格工具白名单与子代理独立 profile 两个候选，先尝试扩展点规避。）

## 6. 仓库关系与版本映射

```text
upstream  https://github.com/openai/codex          （只读）
fork      git@…（深墨 Fork，待创建；本地 /Users/xinyiliu/Desktop/codex-fork 已就绪）
product   xiegang 主仓库                            （不变）
```

- 产品仓库记录它依赖的 fork commit：`backend/app/decision_harness/CODEX_FORK_PIN`
  （文本文件：SHA + 上游 tag + 日期），Adapter 构建/部署脚本读它。
- Fork 的 `origin` 待用户提供远端地址后配置（当前只有 `upstream`）。
- 产品版本 ↔ fork 版本映射在同一提交里更新，保证可追溯。

## 7. 如何同步 upstream

1. `git fetch upstream`；
2. 从当前锚定 tag 建 `shenmo/main` 分支，`git merge upstream/tag rust-vX.Y.Z`
   （**只合并 release tag，不追 main HEAD**——main 上 42 个 feature 处于
   UnderDevelopment，churn 不可控）；
3. 冲突高发区预检（inventory §15）：`app-server-protocol/common.rs`、
   `protocol/protocol.rs`、`core/tools/spec_plan.rs`、`ext/`、MCP 系；
4. 重放 Core Patch 清单（`patches/shenmo/*.patch`，逐个 `git apply --3way`）；
5. 跑升级回归（§8）。

频率建议：跟随上游 release 节奏（约每数周一个 rust-v*.*.*），不追 alpha。

## 8. 上游升级回归测试

最小门禁（每次同步必须全绿）：

1. fork 侧：`cargo check -p codex-app-server`；app-server-test-client 握手冒烟；
2. 协议面：`codex app-server generate-json-schema` 导出快照与上次 diff——
   出现非新增类变更（删字段/改类型）时，必须先升级产品侧 event_bridge/SDK 模型；
3. 产品侧：`SKIP_DB_CONTEXT=1 pytest tests/decision_harness/`（50 个，
   含 CodexAgentRuntime fake-client 全套）+ 全量 `pytest tests/`（384+）；
4. 端到端：FakeAgentRuntime 跑 run_to_plan → 真二进制冒烟（thread/start →
   turn/start → turn/completed）→ 与基线对比事件序列。

## 9. 如何回滚

- 产品侧回滚：`DECISION_RUNTIME_MODE=legacy`（默认值从未改过），LangGraph 链路
  完整保留（spec §19）；
- Fork 回滚：`git reset` 回上一个锚定 tag + 重放 patch 清单 + 恢复
  `CODEX_FORK_PIN`；产品仓库 `git revert` 对应映射提交；
- 二进制回滚：部署脚本保留上一版 codex-app-server 产物，`codex_bin` 指回旧版。

## 10. 如何固定版本

- Fork 内：`rust-toolchain.toml`（1.95.0）+ `Cargo.lock` 随仓库提交；
  开发机不安装额外工具链（默认已指向 1.95.0）；
- 产品内：`CODEX_FORK_PIN`（§6）+ `openai-codex` SDK 版本钉死
  （若替换 `openai-codex-cli-bin`，则连 `sdk/python/pyproject.toml:19` 一起钉）；
- CI：fork 的任何提交必须带与 pin 一致的 SHA 才允许产出部署工件。

## 11. 已验证事项（2026-08-22）

- `cargo check -p codex-app-server` @ 343074d420 通过（macOS arm64，1.95.0，4m21s）；
- `CARGO_PROFILE_DEV_DEBUG=0 cargo build -p codex-app-server` 通过（7m51s；
  本机磁盘曾 98% 满，`cargo clean` 释放 13.1GB 后重建——开发机磁盘需 ≥15GB 余量）；
- **stdio initialize 握手冒烟通过**：自建二进制对
  `{"method":"initialize","params":{"clientInfo":{...}}}` 返回
  `{"id":1,"result":{"userAgent":"…","codexHome":"~/.codex","platformFamily":"unix","platformOs":"macos"}}`
  并发出 `configWarning` 通知（未信任项目目录的正常提醒）；
- Python SDK（openai-codex 0.147.0）与 343074d420 的协议面核对：核心方法/通知
  一致（SDK 落后主线约 3 个 minor，正式落地时二选一：升级 SDK 或锚 0.147.x tag）。

## 12. 风险与开放问题

1. **协议无版本协商**（inventory §8）：跨版本混跑无硬保证——缓解：锚 release tag +
   schema 快照 diff 门禁。
2. **远程压缩仅 OpenAI provider 支持**：非 OpenAI 端点走本地压缩（质量略低）——
   我们的模型策略需在 §13.6 框架内决策。
3. **上游极活跃**：不追 HEAD、只追 tag；扩展点（extension-api）目前稳定使用中，
   但未被官方承诺冻结——若 breaking，按 ext/web-search 同步改造。
4. **深墨 Fork 远端未创建**（按指令不自行创建）——等待用户提供地址。
5. **wire_api=chat 已移除**：若模型端点只支持 Chat Completions，需自建
   Responses API 兼容网关（产品侧，非 core patch）。
