-- Decision Agent Runtime V4 - harness 持久化 schema（spec 2026-08-22 §14）
-- 幂等：全部 CREATE ... IF NOT EXISTS，可重复执行（spec §24.8 前向兼容）。
-- 事件不建新表：继续使用 parliament_events（spec §14.6）。
-- 注意：本迁移不做任何 DROP/ALTER，生产库可安全重放。

-- ================================================
-- 14.1 decision_runs —— 一场决策会议一行（parliament_id 一对一）
-- ================================================
CREATE TABLE IF NOT EXISTS decision_runs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  parliament_id UUID UNIQUE REFERENCES parliaments(id),
  runtime_mode TEXT,
  root_thread_id TEXT,
  workflow_phase TEXT,
  status TEXT,
  state_version INT DEFAULT 1,
  decision_state JSONB,
  active_plan JSONB,
  budget JSONB,
  usage JSONB,
  finalization_state JSONB,
  error TEXT,
  exit_status TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ
);

-- ================================================
-- 14.2 agent_runs —— 主 Agent / 子 Agent 的每次运行
-- ================================================
CREATE TABLE IF NOT EXISTS agent_runs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  decision_run_id UUID NOT NULL REFERENCES decision_runs(id),
  parent_agent_run_id UUID,
  agent_type TEXT NOT NULL,
  expert_id TEXT,
  runtime_provider TEXT,
  runtime_thread_id TEXT,
  task_id TEXT,
  status TEXT,
  input_context_version INT,
  input_context_hash TEXT,
  task_packet JSONB,
  result JSONB,
  budget JSONB,
  usage JSONB,
  error_code TEXT,
  retry_of UUID,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_decision_run ON agent_runs(decision_run_id);

-- ================================================
-- 14.3 plan_versions —— 计划历史必须保留（spec §7.3）
-- ================================================
CREATE TABLE IF NOT EXISTS plan_versions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  decision_run_id UUID NOT NULL REFERENCES decision_runs(id),
  version INT NOT NULL,
  plan JSONB NOT NULL,
  reason TEXT,
  created_by_agent_run_id UUID,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (decision_run_id, version)
);

-- ================================================
-- 14.4 evidence_records —— 共享证据内核（Phase 4 写入，先建表前向兼容）
-- ================================================
CREATE TABLE IF NOT EXISTS evidence_records (
  id TEXT PRIMARY KEY,
  decision_run_id UUID NOT NULL REFERENCES decision_runs(id),
  claim_id TEXT,
  kind TEXT,
  status TEXT,
  source_title TEXT,
  source_url TEXT,
  source_domain TEXT,
  published_at TIMESTAMPTZ,
  retrieved_at TIMESTAMPTZ,
  excerpt TEXT,
  relevance FLOAT,
  reliability FLOAT,
  authority_level TEXT,
  supports_claim TEXT,
  contradicts_claim TEXT,
  metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_evidence_records_run ON evidence_records(decision_run_id);

-- ================================================
-- 14.5 debate_issues —— 实质分歧跟踪（Phase 5 写入，先建表前向兼容）
-- dispute_key 是实现列（非 spec 字段）：DisputeIssue.dispute_id 的稳定键，
-- 供 Phase 5 按 dispute_id 幂等 upsert。
-- ================================================
CREATE TABLE IF NOT EXISTS debate_issues (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  decision_run_id UUID NOT NULL REFERENCES decision_runs(id),
  dispute_key TEXT UNIQUE,
  question TEXT,
  importance FLOAT,
  conflict_type TEXT,
  participants JSONB,
  claims JSONB,
  status TEXT,
  round_count INT,
  resolution JSONB,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_debate_issues_run ON debate_issues(decision_run_id);
