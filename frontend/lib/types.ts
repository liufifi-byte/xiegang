export type MemberId = "suan" | "chen" | "ji" | "nian" | "fan";
export type Stance = "for" | "against" | "neutral";

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

/** 5 议员静态信息（与 backend member_profiles 表保持一致）。 */
export const MEMBERS: Member[] = [
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
  return MEMBERS.find((m) => m.id === id);
}

export interface Evidence {
  claim: string;
  source: string;
  strength: "high" | "medium" | "low";
}

export interface TranscriptEntry {
  speaker: MemberId | "system" | "user";
  round?: number;
  type:
    | "research" | "debate" | "debate_error"
    | "election" | "summary" | "user_interrupt";
  stance?: Stance;
  content: string;
  evidence?: Evidence[];
  confidence?: number;
  /** 群聊时间戳（前端渲染用，后端可选填） */
  timestamp?: string;
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
}

export type MemberStatus = "idle" | "thinking" | "speaking" | "done" | "error";

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
