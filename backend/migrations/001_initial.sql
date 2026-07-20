-- 五贤阁 Council - 初始化 schema
-- 包含 7 张表：users, parliaments, user_memories, topic_templates,
-- member_profiles, parliament_feedback, election_logs

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ================================================
-- 用户表（单用户 MVP 简化）
-- ================================================
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email TEXT,
  display_name TEXT,
  preferences JSONB DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- ================================================
-- 议会主题表（每次议会一行）
-- ================================================
CREATE TABLE IF NOT EXISTS parliaments (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES users(id),

  topic TEXT NOT NULL,
  topic_category TEXT,
  topic_variables JSONB,
  user_background TEXT,

  status TEXT DEFAULT 'pending',
  current_round INT DEFAULT 0,
  tension_score FLOAT,

  members_state JSONB,
  transcript JSONB DEFAULT '[]'::jsonb,
  tool_calls JSONB DEFAULT '[]'::jsonb,

  election_result JSONB,
  summary JSONB,

  total_duration_seconds INT,
  created_at TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ,
  error_message TEXT
);

-- ================================================
-- 跨议会记忆表
-- ================================================
CREATE TABLE IF NOT EXISTS user_memories (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID REFERENCES users(id),
  parliament_id UUID REFERENCES parliaments(id),

  topic_category TEXT,
  topic_summary TEXT,
  topic_embedding VECTOR(1536),

  members_stances JSONB,
  consensus JSONB,
  dissent JSONB,

  user_decision TEXT,
  outcome_rating INT,
  outcome_notes TEXT,

  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_memories_embedding
  ON user_memories USING ivfflat (topic_embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_user_memories_user_cat
  ON user_memories (user_id, topic_category);

-- ================================================
-- 固定议题配置表
-- ================================================
CREATE TABLE IF NOT EXISTS topic_templates (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  category TEXT UNIQUE NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,

  key_variables JSONB,
  historical_cases JSONB,
  domain_prompt TEXT,

  icon TEXT,
  sort_order INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

INSERT INTO topic_templates (category, display_name, description, key_variables, icon, sort_order) VALUES
  ('real_estate',      '买房',     '购房决策',     '["city","price","down_payment","mortgage_rate","hold_period","rental_yield","family_opinion"]'::jsonb, '🏠', 1),
  ('gold',             '买黄金',   '黄金配置',     '["amount","current_price","allocation_ratio","macro_view","exit_plan"]'::jsonb, '🥇', 2),
  ('offer',            '接offer',  '工作评估',     '["base_salary","equity","company_stage","team_size","family_pressure"]'::jsonb, '💼', 3),
  ('job_change',       '跳槽',     '职业转换',     '["current_role","target_role","industry_growth","skill_transfer"]'::jsonb, '🔄', 4),
  ('investment',       '投资',     '一般投资',     '["amount","asset_class","horizon","risk_tolerance"]'::jsonb, '📈', 5),
  ('entrepreneurship', '创业',     '创业决策',     '["idea","team","capital","runway","market_timing"]'::jsonb, '🚀', 6)
ON CONFLICT (category) DO NOTHING;

-- ================================================
-- 议员档案表
-- ================================================
CREATE TABLE IF NOT EXISTS member_profiles (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  color TEXT NOT NULL,
  system_prompt TEXT NOT NULL,
  tools JSONB NOT NULL,
  few_shot_examples JSONB,
  version INT DEFAULT 1,
  is_active BOOLEAN DEFAULT true,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- ================================================
-- 用户决策反馈表
-- ================================================
CREATE TABLE IF NOT EXISTS parliament_feedback (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  parliament_id UUID REFERENCES parliaments(id),
  user_id UUID REFERENCES users(id),

  rating INT,
  surprise_level INT,
  usefulness TEXT,

  created_at TIMESTAMPTZ DEFAULT now()
);

-- ================================================
-- 议长选举日志
-- ================================================
CREATE TABLE IF NOT EXISTS election_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  parliament_id UUID REFERENCES parliaments(id),
  candidates JSONB,
  winner TEXT,
  fallback_used BOOLEAN DEFAULT false,
  created_at TIMESTAMPTZ DEFAULT now()
);
