# 邪杠 P0-S2 方案（Worker 拆分）

- 日期：2026-07-20
- 状态：执行中
- 范围：把 SSE 兼任务执行改为"Worker 跑任务 + SSE 订阅事件"

## 决策

- Worker 架构：Postgres 任务表 + 独立 worker 镜像（不引入 Redis）
- 用户身份：不做（保留 device_id 匿名 + 加最小所有权校验）
- 引擎：Postgres SELECT FOR UPDATE SKIP LOCKED 抢任务

## 子模块

| S | 内容 | 时长 |
|---|---|---|
| S2.1 | parliament_events 表 + event_store.py | 2-3h |
| S2.2 | Worker 主循环 + LangGraph 集成 | 4-6h |
| S2.3 | SSE 改只读 events + device_id 校验 | 1-2h |
| S2.4 | 启动恢复 + 卡死回收 | 2h |
| S2.5 | 部署文档 | 1h |

## 实施记录

| 子项 | commit | 测试 |
|---|---|---|
| S2.1 | （待） | |
| S2.2 | | |
| S2.3 | | |
| S2.4 | | |
| S2.5 | | |
