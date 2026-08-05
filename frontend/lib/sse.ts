import type { AgentTraceEvent, ParliamentSummary, TranscriptEntry } from "./types";
import type { ClarificationQuestion } from "./types";

export interface ParliamentStreamEvent {
  type:
    | "parliament_started"
    | "node_complete"
    | "agent_trace"
    | "expert_completed"
    | "clarification_requested"
    | "clarification_answered"
    | "clarification_skipped"
    | "clarification_resumed"
    | "parliament_done"
    | "cancelled"
    | "error";
  parliament_id?: string;
  seq?: number;
  topic?: string;
  node?: string;
  error?: string;
  trace_id?: AgentTraceEvent["trace_id"];
  run_id?: AgentTraceEvent["run_id"];
  agent_id?: AgentTraceEvent["agent_id"];
  step?: AgentTraceEvent["step"];
  phase?: AgentTraceEvent["phase"];
  public_message?: AgentTraceEvent["public_message"];
  public_intent?: AgentTraceEvent["public_intent"];
  tool_name?: AgentTraceEvent["tool_name"];
  display_query?: AgentTraceEvent["display_query"];
  source_previews?: AgentTraceEvent["source_previews"];
  evidence_count?: AgentTraceEvent["evidence_count"];
  duration_ms?: AgentTraceEvent["duration_ms"];
  cache_hit?: AgentTraceEvent["cache_hit"];
  timestamp?: AgentTraceEvent["timestamp"];
  expert_id?: string;
  member?: Record<string, any>;
  entry?: TranscriptEntry;
  completed_count?: number;
  total_count?: number;
  questions?: ClarificationQuestion[];
  clarification_status?: string;
  answers?: Array<Record<string, unknown>>;
  payload?: {
    // state_update 子集（前端实际用到的）
    category?: string;
    briefing?: { key_variables: string[]; summary: string };
    members?: Record<string, any>;
    transcript?: TranscriptEntry[];
    current_round?: number;
    tension_score?: number;
    election_result?: {
      winner: string;
      fallback_used: boolean;
      candidates: any[];
    };
    summary?: ParliamentSummary;
    [k: string]: any;
  };
}

/**
 * 后端 SSE 直连 URL。
 *
 * 本地开发直接打 http://127.0.0.1:8001（绕开 Next.js dev rewrite
 * 对 SSE 的缓冲问题）。
 * 生产部署时通过 NEXT_PUBLIC_API_URL 环境变量覆盖。
 *
 * 注意：用 ?? 而非 || —— 避免生产环境显式设为空字符串
 * (NEXT_PUBLIC_API_URL="") 时被 || 误判为 falsy 而取 localhost fallback，
 * 导致线上访问 127.0.0.1:8001 失败。
 */
const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8001";

/**
 * 订阅议会 SSE 流。
 * 返回 unsubscribe 函数。
 *
 * Codex P0 fix 版本:
 * - EventSource 标准 API 不支持自定义 header → device_id 通过 query 传
 * - Cloudflare 100s 会主动断 → 无限静默重连(reconnectCount 上限 200)
 * - 重连成功→重置 reconnectCount=0(P0-6)
 * - last_seq 用 URLSearchParams 拼接, 无 device_id 时也正确(P0-7)
 */
export function subscribeParliament(
  parliamentId: string,
  onEvent: (event: ParliamentStreamEvent) => void,
  onError?: (error: Error) => void
): () => void {
  let deviceId = "";
  try {
    deviceId =
      typeof localStorage !== "undefined"
        ? localStorage.getItem("xiegang_device_id") || ""
        : "";
  } catch {
    // localStorage 不可用(Safari 隐私检 / SSR), 走空让后端 fallback IP
  }

  let eventSource: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let terminalReceived = false;
  let maxSeq = 0;
  let consecutiveFailures = 0; // P0-6: 重连成功后重置
  const MAX_CONSECUTIVE_FAILURES = 200; // 足够覆盖 1 小时

  function buildUrl(): string {
    // P0-7: 用 URLSearchParams 正确拼接, 无论有没有 device_id 都对
    const params = new URLSearchParams();
    if (deviceId) params.set("device_id", deviceId);
    params.set("last_seq", String(maxSeq));
    const qs = params.toString();
    return `${API_BASE}/api/parliament/${parliamentId}/stream?${qs}`;
  }

  const connect = () => {
    if (stopped || terminalReceived) return;
    const source = new EventSource(buildUrl());
    eventSource = source;

    source.onopen = () => {
      // P0-6: 重连成功 → 重置连续失败计数
      consecutiveFailures = 0;
    };

    source.onmessage = (e) => {
      try {
        const event: ParliamentStreamEvent = JSON.parse(e.data);
        // 记住 max seq 供下次重连断点续传
        if (typeof event.seq === "number" && event.seq > maxSeq) {
          maxSeq = event.seq;
        }
        onEvent(event);
        if (
          event.type === "parliament_done" ||
          event.type === "cancelled" ||
          event.type === "error"
        ) {
          terminalReceived = true;
          source.close();
        }
      } catch {
        // JSON parse 失败, 不打断议会
      }
    };

    source.onerror = () => {
      source.close();
      if (stopped || terminalReceived) return;
      // Cloudflare 断开 = 正常;收到任何有效事件就重置 consecutiveFailures
      consecutiveFailures += 1;
      if (consecutiveFailures > MAX_CONSECUTIVE_FAILURES) {
        stopped = true;
        onError?.(new Error("连接持续不稳定，会议可能仍在后台运行。请刷新页面。"));
        return;
      }
      // 1-3s 指数退避
      const delay = Math.min(3000, 800 * 1.3 ** Math.min(consecutiveFailures, 8));
      reconnectTimer = setTimeout(connect, delay);
    };
  };

  connect();

  return () => {
    stopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    eventSource?.close();
  };
}
