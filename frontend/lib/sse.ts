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
 * EventSource 标准 API 不支持自定义 header,所以 device_id 通过 query 传
 * (后端 stream.py 优先从 query_params 读)。
 */
export function subscribeParliament(
  parliamentId: string,
  onEvent: (event: ParliamentStreamEvent) => void,
  onError?: (error: Error) => void
): () => void {
  // 拿 device_id (localStorage 里的稳定 UUID, 或 fallback 用 ip:xxx)
  let deviceId = "";
  try {
    deviceId =
      typeof localStorage !== "undefined"
        ? localStorage.getItem("xiegang_device_id") || ""
        : "";
  } catch {
    // localStorage 不可用(Safari 隐私检 / SSR), 走空让后端 fallback IP
  }
  const qs = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : "";
  let url = `${API_BASE}/api/parliament/${parliamentId}/stream${qs}`;
  let eventSource: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  let terminalReceived = false;
  let maxSeq = 0; // PRD fix: 记住最后收到的 seq, 重连时从断点续传
  // PRD fix: Cloudflare 100s 会主动断 SSE → 这是常态,不是错误!
  //          前端必须无限重连(只要没收到 terminal 事件就继续),
  //          12 分钟议会期间可能 20+ 次断开重连, 全程对用户透明.
  let reconnectCount = 0;
  const MAX_RECONNECT = 60; // 12 分钟议会 × 每 30s 心跳间隔 ≈ 24 次, 给 60 余量

  const connect = () => {
    if (stopped || terminalReceived) return;
    // 重连时把 maxSeq 传给后端 → 后端从 last_seq 续传(CF 100s 断开后的断点恢复)
    const currentUrl =
      reconnectCount === 0
        ? url
        : `${url}&last_seq=${maxSeq}`;
    const source = new EventSource(currentUrl);
    eventSource = source;

    source.onopen = () => {
      // 重连成功, 不算失败
    };

    source.onmessage = (e) => {
      try {
        const event: ParliamentStreamEvent = JSON.parse(e.data);
        // 记住 max seq 供下次重连使用
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
      } catch (err) {
        // JSON parse 失败, 不打断议会
      }
    };

    source.onerror = () => {
      source.close();
      if (stopped || terminalReceived) return;
      // PRD fix: Cloudflare 断开 = 正常, 不再用 failedAttempts 计数
      // 只要议会状态不是 terminal 就无限重连, 间隔 1-3s 指数退避
      reconnectCount += 1;
      if (reconnectCount > MAX_RECONNECT) {
        stopped = true;
        onError?.(new Error("连接持续不稳定，已停止自动重连"));
        return;
      }
      const delay = Math.min(3000, 1000 * 1.5 ** Math.min(reconnectCount - 1, 5));
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
