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
 */
const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";

/**
 * 订阅议会 SSE 流。
 * 返回 unsubscribe 函数。
 */
export function subscribeParliament(
  parliamentId: string,
  onEvent: (event: ParliamentStreamEvent) => void,
  onError?: (error: Error) => void
): () => void {
  const url = `${API_BASE}/api/parliament/${parliamentId}/stream`;
  let eventSource: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let terminalReceived = false;
  let failedAttempts = 0;

  const connect = () => {
    if (stopped || terminalReceived) return;
    const source = new EventSource(url);
    eventSource = source;

    source.onopen = () => {
      failedAttempts = 0;
    };

    source.onmessage = (e) => {
      try {
        const event: ParliamentStreamEvent = JSON.parse(e.data);
        onEvent(event);
        if (
          event.type === "parliament_done" ||
          event.type === "cancelled" ||
          event.type === "error"
        ) {
          terminalReceived = true;
          source.close();
        }
      } catch (err) {
        onError?.(err as Error);
      }
    };

    source.onerror = () => {
      source.close();
      if (stopped || terminalReceived) return;
      failedAttempts += 1;
      if (failedAttempts >= 5) {
        stopped = true;
        onError?.(new Error("SSE 连接出错"));
        return;
      }
      const delay = Math.min(5000, 750 * 2 ** (failedAttempts - 1));
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
