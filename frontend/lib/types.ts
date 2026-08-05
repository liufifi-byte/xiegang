export type ExpertId =
  | "mathematician"
  | "economist"
  | "psychologist"
  | "historian"
  | "artist"
  | "politician";
export type LegacyMemberId = "suan" | "chen" | "ji" | "nian" | "fan";
export type MemberId = ExpertId | LegacyMemberId;
export type Stance = "for" | "against" | "neutral";
export type DecisionPosition =
  | "support"
  | "oppose"
  | "defer"
  | "conditional_support"
  | "undetermined";
export type BasisLevel = "limited" | "moderate" | "strong";
export type EvidenceKind =
  | "user_fact"
  | "web_source"
  | "calculation"
  | "inference";

export interface Member {
  id: MemberId;
  display_name: string;
  color: string;
  role: string;
  /** 单字头（"算"/"沉"/"机"/"年"/"对"），群聊头像用 */
  avatar_char: string;
  /** V1-S4 思维模型：变成 hover tooltip 时显示的详细角色定位 */
  model: string;
  /** V1-S4 标志句式：每议员的招牌问句 */
  signature_line: string;
}

/** 六位职业专家；每场由后端选择五位。 */
export const MEMBERS: Member[] = [
  {
    id: "mathematician", display_name: "数学家", color: "#6F4878", role: "决策科学", avatar_char: "数",
    model: "决策理论 · 概率 · 敏感性分析",
    signature_line: "改变哪个变量，会让答案反过来？",
  },
  {
    id: "economist", display_name: "经济学家", color: "#9B4D68", role: "经济分析", avatar_char: "经",
    model: "机会成本 · 激励机制 · 边际收益",
    signature_line: "你真正放弃的那个选项，值多少钱？",
  },
  {
    id: "psychologist", display_name: "心理学家", color: "#A45B52", role: "心理分析", avatar_char: "心",
    model: "价值澄清 · 认知偏差 · 身份认同",
    signature_line: "这是你真正想要的，还是你害怕失去的？",
  },
  {
    id: "historian", display_name: "历史学家", color: "#7B6652", role: "历史分析", avatar_char: "史",
    model: "历史类比 · 基准概率 · 路径依赖",
    signature_line: "过去真的发生过同一件事吗？",
  },
  {
    id: "artist", display_name: "艺术家", color: "#86527D", role: "创意重构", avatar_char: "艺",
    model: "问题重构 · 第三选项 · 意义建构",
    signature_line: "如果不接受这个二选一，第三条路是什么？",
  },
  {
    id: "politician", display_name: "政治家", color: "#745B70", role: "博弈分析", avatar_char: "政",
    model: "利益相关者 · 权力关系 · 谈判",
    signature_line: "谁真正握有筹码，你还能谈什么？",
  },
];

/** 仅用于兼容已经归档的旧议会。 */
export const LEGACY_MEMBERS: Member[] = [
  {
    id: "suan", display_name: "老算", color: "#2B4F8C", role: "理性", avatar_char: "算",
    model: "第一性原理 · 期望值 · 折现率",
    signature_line: "这个决定期望值多少？你的折现率多少？",
  },
  {
    id: "chen", display_name: "老沉", color: "#DC3F4B", role: "保守", avatar_char: "沉",
    model: "风险评估 · 反脆弱 · 损失厌恶",
    signature_line: "最坏情况你能扛多久？你的安全垫够吗？",
  },
  {
    id: "ji", display_name: "小机", color: "#E8B441", role: "机会", avatar_char: "机",
    model: "机会成本 · 时间窗口 · 沉没成本切割",
    signature_line: "这个窗口期 12 个月还是 24 个月？你的最佳替代是什么？",
  },
  {
    id: "nian", display_name: "十年", color: "#1E7C5B", role: "长期", avatar_char: "年",
    model: "复利 · 路径依赖 · 身份认同",
    signature_line: "10 年后的你回看这个决定会怎么评？",
  },
  {
    id: "fan", display_name: "反对", color: "#5C3D6E", role: "反共识", avatar_char: "反",
    model: "逆向思考 · 流行偏见识别 · 边缘视角",
    signature_line: "这个共识里有没有盲点？历史什么时候被打脸？",
  },
];

export function getMember(id: string): Member | undefined {
  return [...MEMBERS, ...LEGACY_MEMBERS].find((m) => m.id === id);
}

export interface Evidence {
  claim: string;
  source: string;
  strength: "high" | "medium" | "low";
  evidence_id?: string;
  kind?: EvidenceKind;
  source_title?: string;
  source_url?: string | null;
  source_domain?: string;
  supports_claim?: string;
  calculation_inputs?: Record<string, unknown>;
  calculation_formula?: string | null;
  assumptions?: string[];
}

export type AgentTracePhase =
  | "started"
  | "deciding"
  | "tool_started"
  | "tool_completed"
  | "evidence_added"
  | "finalizing"
  | "completed"
  | "degraded"
  | "failed";

export interface AgentSourcePreview {
  title: string;
  domain?: string;
  url?: string | null;
}

export interface AgentTraceEvent {
  seq?: number;
  trace_id: string;
  run_id: string;
  agent_id: MemberId;
  step: number;
  phase: AgentTracePhase;
  public_message: string;
  public_intent?: string | null;
  tool_name?: string | null;
  display_query?: string | null;
  source_previews?: AgentSourcePreview[];
  evidence_count?: number | null;
  duration_ms?: number | null;
  cache_hit?: boolean | null;
  timestamp: number;
}

export interface TranscriptEntry {
  speaker: MemberId | "system" | "user";
  round?: number;
  type:
    | "research" | "debate" | "debate_error"
    | "election" | "summary" | "user_interrupt" | "user_clarification";
  stance?: Stance;
  content: string;
  evidence?: Evidence[];
  confidence?: number;
  position?: DecisionPosition;
  basis_level?: BasisLevel;
  duty?: string;
  reply_to?: string;
  /** 群聊时间戳（前端渲染用，后端可选填） */
  timestamp?: string;
}

export interface ClarificationQuestion {
  id: string;
  question: string;
  why_it_matters: string;
  answer_type: "single_choice" | "number" | "short_text";
  options: string[];
  impact: "high";
  source: "user_only";
}

export interface ElectionCandidate {
  member_id: string;
  recommend_target: string;
  target_specific_view: string;
  target_blind_spot: string;
  own_bias: string;
  synthesis_method: string;
  score: number;
}

export interface ElectionResult {
  candidates: ElectionCandidate[];
  vote_tally: Record<string, number>;
  recommenders: Record<string, string[]>;
  winner: string;
  fallback_used: boolean;
  fallback_reason: string | null;
}

export interface ParliamentSummary {
  consensus: string[];
  dissent: Array<{ topic: string; sides: Record<string, string> }>;
  blind_spots: string[];
  advice: string;
  open_questions: string[];
  chair?: string;
  chair_name?: string;
  fallback_used?: boolean;
  /** V1-S1.2 新增：完整选举详情 */
  election?: ElectionResult;
  /** V1-S1.2 新增：议员立场分布 [member_id, stance, confidence] */
  member_stances?: Record<string, { stance: Stance; confidence: number }>;
  selected_expert_ids?: MemberId[];
  expert_selection_reasons?: Record<string, string>;
  agent_results?: Record<
    string,
    {
      result: {
        stance: Stance;
        confidence: number;
        thesis: string;
        evidence_refs?: string[];
        assumptions?: string[];
        unknowns?: string[];
        reversal_conditions?: string[];
        next_actions?: string[];
        degraded?: boolean;
        degradation_reason?: string | null;
      };
      evidence?: Array<{
        evidence_id: string;
        claim_summary: string;
        source_title: string;
        source_url?: string | null;
        source_domain?: string;
        strength: "high" | "medium" | "low";
      }>;
      metrics?: {
        reason_steps?: number;
        tool_calls?: number;
        duration_ms?: number;
      };
    }
  >;
  research_degraded?: boolean;
  research_error?: string | null;
  recommendation?: string;
  recommendation_strength?: "conditional" | "clear" | "insufficient";
  key_reason?: string;
  decision_options?: Array<{
    option: string;
    benefits: string[];
    costs: string[];
  }>;
  assumptions?: string[];
  next_actions?: string[];
  reversal_conditions?: string[];
  round_count?: number;
  member_positions?: Array<{
    expert_id: string;
    stance: Stance;
    position?: DecisionPosition | null;
    basis_level?: BasisLevel;
  }>;
  core_disputes?: Array<{
    dispute_id: string;
    question: string;
    kind?: string;
    participants?: string[];
    status?: string;
    pressure_test?: boolean;
  }>;
  basis_assessment?: {
    level: BasisLevel;
    verified_count: number;
    important_unknown_count: number;
    description?: string;
  };
  analysis_basis?: {
    user_facts: SummaryEvidence[];
    web_sources: SummaryEvidence[];
    calculations: SummaryEvidence[];
    inferences: SummaryEvidence[];
  };
}

export interface SummaryEvidence {
  evidence_id: string;
  content: string;
  supports_claim?: string;
  source_title?: string;
  source_url?: string | null;
  source_domain?: string;
  published_at?: string | null;
  freshness_note?: string | null;
  calculation_inputs?: Record<string, unknown>;
  calculation_formula?: string | null;
  assumptions?: string[];
  expert_id?: string;
}

export type MemberStatus =
  | "idle"
  | "thinking"
  | "using_tool"
  | "organizing"
  | "speaking"
  | "done"
  | "degraded"
  | "error";

/** 8 个固定场景（与后端 topic_templates 表的 sort_order 一致） */
export interface TopicTemplate {
  category: string;
  display_name: string;
  /** 短描述，2-4 字用于场景卡片副标 */
  hint: string;
  description?: string;
}

export const TOPIC_TEMPLATES: TopicTemplate[] = [
  { category: "real_estate",      display_name: "买房",     hint: "居住" },
  { category: "offer",            display_name: "接 offer", hint: "工作" },
  { category: "job_change",       display_name: "跳槽",     hint: "职业" },
  { category: "investment",       display_name: "投资",     hint: "资产" },
  { category: "entrepreneurship", display_name: "创业",     hint: "风险" },
  { category: "gold",             display_name: "买黄金",   hint: "避险" },
  { category: "stocks",           display_name: "买股票",   hint: "个股" },
  { category: "buy_bag",          display_name: "买包",     hint: "消费" },
];

// ============================================================
// PRD v2: 决策路径(Decision Path)
// ============================================================

export type DecisionQuestionType =
  | "single_choice"
  | "multi_choice"
  | "range_choice"
  | "choice_with_custom"
  | "search_select"
  | "number"
  | "short_text";

export interface DecisionQuestionOption {
  value: string;
  label: string;
  description?: string;
}

export interface DecisionQuestion {
  id: string;
  question: string;
  why_it_matters: string;
  answer_type: DecisionQuestionType;
  options: DecisionQuestionOption[];
  allow_custom?: boolean;
  custom_placeholder?: string;
  unknown_assumption?: string;
}

export interface DecisionBrief {
  decision_goal: string;
  decision_options: string[];
  analysis_dimensions: string[];
  autonomous_research: string[];
  final_deliverables: string[];
}

export interface ExtractedFact {
  id: string;
  label: string;
  value: string;
  source?: string;
  editable?: boolean;
}

export interface DecisionAssumption {
  question_id: string;
  assumption_text: string;
  conclusion_flip_condition?: string;
}

export interface DecisionPathPreview {
  version: string;
  generation_id: string;
  category: string;
  brief: DecisionBrief;
  extracted_facts: ExtractedFact[];
  questions: DecisionQuestion[];
  assumptions: DecisionAssumption[];
  fallback_used?: boolean;
}

export interface DecisionAnswerItem {
  question_id: string;
  /** single/range/number: string; multi: string[] */
  value: string | string[];
  custom_value?: string | null;
}

/** 前端 modal 状态机(PRD §12.1) */
export type DecisionPathStatus =
  | "idle"
  | "generating"
  | "ready"
  | "revising"
  | "confirmed"
  | "creating_meeting"
  | "create_failed";
