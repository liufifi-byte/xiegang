"""migrate_013_decision_path_tables.sql - 决策路径草稿表(行内 SQL, 供脚本 import)

对应 PRD §13.5 / §13.6 / §13.7
"""
CREATE_TABLE_DECISION_PATH_DRAFTS = """
CREATE TABLE IF NOT EXISTS decision_path_drafts (
  id text primary key,
  device_id text not null,
  version text not null,
  topic text not null,
  topic_category text,
  user_background text,
  selected_expert_ids jsonb not null default '[]'::jsonb,
  payload jsonb not null,
  revision integer not null default 1,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  expires_at timestamptz not null default now() + interval '24 hours',
  consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_decision_path_drafts_device
  ON decision_path_drafts(device_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_decision_path_drafts_expires
  ON decision_path_drafts(expires_at);
"""

ALTER_PARLIAMENTS_ADD_DECISION_PATH = """
ALTER TABLE parliaments
  ADD COLUMN IF NOT EXISTS decision_path jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS decision_path_status text NOT NULL DEFAULT 'not_started',
  ADD COLUMN IF NOT EXISTS decision_path_confirmed_at timestamptz,
  ADD COLUMN IF NOT EXISTS awaiting_input_reason text,
  ADD COLUMN IF NOT EXISTS idempotency_key text;
CREATE UNIQUE INDEX IF NOT EXISTS uq_parliaments_idempotency_key
  ON parliaments(idempotency_key) WHERE idempotency_key IS NOT NULL;
"""

CREATE_TABLE_PARLIAMENT_QUESTIONS = """
CREATE TABLE IF NOT EXISTS parliament_questions (
  id uuid primary key,
  parliament_id uuid not null references parliaments(id),
  stage text not null,
  trigger_source text not null,
  question jsonb not null,
  answer jsonb,
  status text not null,
  blocking_scope jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  answered_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_parliament_questions_parliament
  ON parliament_questions(parliament_id);
CREATE INDEX IF NOT EXISTS idx_parliament_questions_pending
  ON parliament_questions(parliament_id) WHERE status = 'pending';
"""


ALL_MIGRATIONS = (
    CREATE_TABLE_DECISION_PATH_DRAFTS,
    ALTER_PARLIAMENTS_ADD_DECISION_PATH,
    CREATE_TABLE_PARLIAMENT_QUESTIONS,
)
