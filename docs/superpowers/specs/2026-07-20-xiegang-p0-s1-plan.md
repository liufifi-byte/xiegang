# 邪杠 P0-S1 方案（防烧火优先）

- 日期：2026-07-20
- 状态：执行中
- 范围：让产品具备"真实用户可以长期依赖"的最小资格

## 探查发现 5 个隐性 bug（必须先修）

| # | 现 | 现实（验证后） |
|---|---|---|
| 1 | archive.py `category` 字段 | 写入不存在的列，postgrest 静默丢弃 → 应为 `topic_category` |
| 2 | VALID_CATEGORIES | `stocks` / `buy_bag` 不在白名单 → 全被强转 `general` |
| 3 | parliament_runtime 表 | V2-S1 创建是否成功未确认，先验证 |
| 4 | archive.py update_data | 漏 `finished_at` + `total_duration_seconds`，DB 永远 NULL |
| 5 | 节点异常处理 | 只发 error 事件，parliaments.status 永远 `running` |

## 验收目标

- 100 场连跑 ≥ 90% 完成率
- 单 IP/device_id 每天最多 10 场
- 单场 ≤ 8 分钟
- 节点炸 → status="failed" 不卡 running
- archive 真写全字段
- CORS env 可配
- 客户端断开 60s 内不再烧 LLM 钱

## S1 子模块

| S | 内容 | 时长 |
|---|---|---|
| **S1.0** | 修 5 个 bug | 1.5-2h |
| **S1.1** | IP + device_id 限流 | 2-3h |
| **S1.2** | LLM 硬上限 + 节点熔断 | 2h |
| **S1.3** | 客户端断开真硬停 | 1-2h |
| **S1.4** | CORS env + 监控 | 1h |
| **S1.5** | 100 场冒烟（可选） | 2-3h |

## 留给后续 S 的债

- P0 #2 Worker 拆分（任务队列） → S2
- P0 #3 真用户身份（微信登录） → S2
- P0 #4 隐私内容安全 → S3
- P0 #1.1 100 场 98% 完成率 → S2（要 Tavily / Supabase 持续稳定）

## 实施记录

| 子项 | commit | 测试 |
|---|---|---|
| S1.0 bug 修复 | （待） | 122 |
| S1.1 限流 | | |
| S1.2 LLM 上限 | | |
| S1.3 断开真停 | | |
| S1.4 CORS+监控 | | |
| S1.5 冒烟 | | |
