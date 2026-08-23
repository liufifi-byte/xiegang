/**
 * Decision Harness（V4）§15.1 产品事件 → 用户可理解的卡片。
 *
 * 铁律（spec §15.2）：
 * - 用户应看到：计划、专家任务、正在核实的事实、争议、来源接受/拒绝、
 *   是否等待用户、局部失败如何降级；
 * - 用户不应看到：隐藏推理、系统 Prompt、工具参数、原始搜索结果、堆栈。
 *
 * 纯函数映射，无 React 依赖；未知事件返回 null（由调用方忽略）。
 */

export type HarnessEventKind =
  | "plan"
  | "expert"
  | "dispute"
  | "evidence"
  | "audit"
  | "status"
  | "final"
  | "user";

export interface HarnessCard {
  kind: HarnessEventKind;
  /** 一行标题（用户语言，无技术术语） */
  title: string;
  /** 补充说明（可为空） */
  detail?: string;
  /** 卡片内的小标签（如 立场/状态） */
  tags?: string[];
  /** 是否需要用户注意（等待输入/结论出炉） */
  attention?: boolean;
}

interface RawEvent {
  type?: string;
  event_type?: string;
  payload?: Record<string, any>;
}

const EXPERT_NAMES: Record<string, string> = {
  economist: "经济学家",
  psychologist: "心理学家",
  historian: "历史学家",
  mathematician: "数学家",
  strategist: "策略家",
  legal_scholar: "法律学者",
  sociologist: "社会学家",
  contrarian: "唱反调的人",
};

const STANCE_LABELS: Record<string, string> = {
  support: "支持",
  oppose: "反对",
  cautious: "谨慎",
  neutral: "中立",
  defer: "暂不表态",
  conditional_support: "有条件支持",
};

function expertName(id?: string): string {
  return EXPERT_NAMES[id || ""] || id || "专家";
}

function stanceLabel(s?: string): string {
  return STANCE_LABELS[s || ""] || s || "";
}

/** §15.1 事件 → 卡片；非 harness 事件 / 需静默的事件 → null */
export function describeHarnessEvent(event: RawEvent): HarnessCard | null {
  const type = event.event_type || event.type || "";
  const p = event.payload || {};

  switch (type) {
    // ---- 计划 ----
    case "plan_created":
      return {
        kind: "plan",
        title: "主席已制定研究计划",
        detail: p.objective ? `目标：${String(p.objective).slice(0, 120)}` : undefined,
        tags: Array.isArray(p.expert_tasks) && p.expert_tasks.length
          ? [`拟请 ${p.expert_tasks.length} 位专家`]
          : undefined,
      };
    case "plan_revised":
      return {
        kind: "plan",
        title: `研究计划已更新（第 ${p.plan_version ?? "?"} 版）`,
        detail: p.objective ? `新目标：${String(p.objective).slice(0, 120)}` : undefined,
      };

    // ---- 专家 ----
    case "agent_task_created":
      return {
        kind: "expert",
        title: `${expertName(p.agent_id)}领到任务`,
        detail: p.objective ? String(p.objective).slice(0, 120) : undefined,
      };
    case "agent_started":
      return { kind: "expert", title: `${expertName(p.agent_id)}开始分析` };
    case "agent_partial":
      return null; // 中间流不逐条打扰，聚合在任务卡里
    case "agent_completed":
      return {
        kind: "expert",
        title: `${expertName(p.agent_id)}完成${p.followup ? "补充回答" : "分析"}`,
        tags: [stanceLabel(p.stance), p.status === "partial" ? "有限结论" : ""]
          .filter(Boolean),
      };
    case "agent_failed":
      return {
        kind: "expert",
        title: `${expertName(p.agent_id)}暂时没能完成`,
        detail: "这一路分析受阻，会议继续，其余专家照常进行。",
      };

    // ---- 争议 ----
    case "dispute_opened":
      return {
        kind: "dispute",
        title: "专家之间出现实质分歧",
        detail: p.question ? String(p.question).slice(0, 140) : undefined,
        tags: Array.isArray(p.participants)
          ? [p.participants.map((i: string) => expertName(i)).join(" vs ")]
          : undefined,
      };
    case "debate_message":
      return {
        kind: "dispute",
        title: `${expertName(p.agent_id)}回应了质询`,
        tags: [
          p.changed ? "调整了立场" : "坚持原看法",
          stanceLabel(p.position_after),
        ].filter(Boolean),
      };
    case "dispute_resolved":
      return {
        kind: "dispute",
        title: "分歧已厘清",
        detail: p.rounds != null ? `经过 ${p.rounds} 轮交叉质询` : undefined,
      };

    // ---- 证据 ----
    case "evidence_added":
      return {
        kind: "evidence",
        title: `核实了一条关键信息`,
        detail: p.source_domain ? `来源：${p.source_domain}` : undefined,
        tags: ["已验证"],
      };
    case "evidence_rejected":
      return {
        kind: "evidence",
        title: "一条来源被弃用",
        detail:
          p.reason === "stale" ? "信息已过时" :
          p.reason === "low_reliability" ? "来源可靠性不足" :
          p.reason === "web_source_without_url" ? "无法追溯原始出处" :
          p.reason,
      };

    // ---- 审计 ----
    case "audit_started":
      return { kind: "audit", title: "正在核查所有引用的可靠性" };
    case "audit_passed":
      return { kind: "audit", title: "引用核查通过", tags: ["全部可用"] };
    case "audit_failed":
      return {
        kind: "audit",
        title: "部分引用未通过核查",
        detail: "结论只保留有可靠依据的部分。",
      };

    // ---- 用户 ----
    case "user_input_requested":
      return {
        kind: "user",
        title: "需要你补充一点信息",
        detail: Array.isArray(p.questions) && p.questions.length
          ? p.questions.slice(0, 3).map((q: string) => `· ${q}`).join("\n")
          : undefined,
        attention: true,
      };

    // ---- 状态与终局 ----
    case "runtime_recovered":
      return {
        kind: "status",
        title: "会议从中断处恢复了",
        detail: "刚才的进度都已保留。",
      };
    case "finalization_proposed":
      return { kind: "final", title: "正在汇总结论" };
    case "decision_finalized": {
      // 主席统一结论卡（四段：建议/已确认/推进条件/下一步）优先展示
      const summary = typeof p.chair_summary === "string"
        ? p.chair_summary.slice(0, 600) : undefined;
      const strengthHint = p.strength === "clear" ? "结论明确" :
        p.strength === "conditional" ? "结论带适用条件，请注意查看" :
        p.strength === "insufficient" ? "信息不足，报告列出了待确认事项" : undefined;
      return {
        kind: "final",
        title: "决策报告已生成",
        detail: summary || strengthHint,
        tags: strengthHint ? [strengthHint] : undefined,
        attention: true,
      };
    }
    case "partial_finalized":
      return {
        kind: "final",
        title: "已生成阶段性结论",
        detail: "预算或信息有限，报告说明了已确认部分与存疑部分。",
        attention: true,
      };

    // ---- run_log：只翻译需要用户理解的降级语义 ----
    case "run_log": {
      const note = p.note || "";
      if (note === "research_degraded")
        return {
          kind: "status",
          title: "网络检索暂时不可用",
          detail: "会议将基于已确认的信息继续，结论会标注相应限制。",
        };
      if (note === "dispute_retained")
        return {
          kind: "dispute",
          title: "一处分歧经讨论后保留",
          detail: "两边论点都会写进报告，供你自己权衡。",
        };
      if (note === "dispute_narrowed")
        return {
          kind: "dispute",
          title: "分歧聚焦到一个待确认的点",
          detail: p.missing_variable ? `关键点：${p.missing_variable}` : undefined,
        };
      if (note === "expert_selection")
        return {
          kind: "status",
          title: "专家名单微调",
          detail: String(p.detail || "").slice(0, 120),
        };
      if (note === "experts_complete" || note === "debate_complete"
          || note === "research_complete" || note === "claim_verdict"
          || note === "phase" || note === "planning_complete")
        return null; // 阶段切换噪声不展示
      if (note === "run_cancelled")
        return { kind: "status", title: "会议已按你的要求停止", detail: "已完成的结论保留。" };
      return null;
    }

    default:
      return null; // legacy 事件与其他未知类型：交回旧渲染管线
  }
}

/** §15.1 全集（新 runtime 事件名）。用于 SSE 白名单分流。 */
export const HARNESS_EVENT_TYPES: ReadonlySet<string> = new Set([
  "plan_created", "plan_revised", "user_input_requested", "agent_task_created",
  "agent_started", "agent_action", "agent_tool_started", "agent_tool_completed",
  "agent_partial", "agent_completed", "agent_failed", "dispute_opened",
  "debate_message", "dispute_resolved", "evidence_added", "evidence_rejected",
  "audit_started", "audit_failed", "audit_passed", "finalization_proposed",
  "partial_finalized", "decision_finalized", "runtime_recovered", "run_log",
]);
