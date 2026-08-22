import type { AgentTraceEvent, ParliamentSummary, TranscriptEntry } from "./types";
import type { ClarificationQuestion } from "./types";

export interface ParliamentStreamEvent {
  type:
    | "parliament_started"
    // ---- V4 Decision Harness（spec §15.1）产品事件 ----
    | "plan_created"
    | "plan_revised"
    | "user_input_requested"
    | "agent_task_created"
    | "agent_started"
    | "agent_action"
    | "agent_tool_started"
    | "agent_tool_completed"
    | "agent_partial"
    | "agent_completed"
    | "agent_failed"
    | "dispute_opened"
    | "debate_message"
    | "dispute_resolved"
    | "evidence_added"
    | "evidence_rejected"
    | "audit_started"
    | "audit_failed"
    | "audit_passed"
    | "finalization_proposed"
    | "partial_finalized"
    | "decision_finalized"
    | "runtime_recovered"
    | "run_log"
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
 * Codex P0 fix: 全部改为 POST, 避免 EdgeOne webblock 对 GET 的拦截。
 * 不再用 EventSource(只支持 GET), 改用 fetch POST + ReadableStream 手动解析 SSE 格式。
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
    // localStorage 不可用
  }

  let stopped = false;
  let terminalReceived = false;
  let maxSeq = 0;
  let consecutiveFailures = 0;
  const MAX_CONSECUTIVE_FAILURES = 200;
  let abortController: AbortController | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  async function connect() {
    if (stopped || terminalReceived) return;
    abortController = new AbortController();
    const params = new URLSearchParams();
    if (deviceId) params.set("device_id", deviceId);
    params.set("last_seq", String(maxSeq));

    try {
      const resp = await fetch(
        `${API_BASE}/api/parliament/${parliamentId}/stream?${params}`,
        {
          method: "POST",
          headers: { Accept: "text/event-stream" },
          signal: abortController.signal,
        }
      );

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      // 重连成功 → 重置计数
      consecutiveFailures = 0;

      // 手动解析 SSE 格式(line-by-line 状态机, 比按 \n\n 切分更鲁棒)
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let pendingData = "";

      const emitEvent = (dataStr: string) => {
        try {
          const event: ParliamentStreamEvent = JSON.parse(dataStr);
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
            reader.cancel();
          }
        } catch {
          // JSON parse 失败, 跳过
        }
      };

      while (!stopped && !terminalReceived) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // 按 line 处理. SSE 事件 = 连续 "data:" 行, 空行(\n) 触发 emit.
        // "event:" 行(如 ping) 被忽略不是问题(我们只看 data).
        let idx: number;
        while ((idx = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, idx).replace(/\r$/, "");
          buffer = buffer.slice(idx + 1);

          if (line === "") {
            // 空行 = 事件分隔符 → emit buffered data
            if (pendingData) {
              emitEvent(pendingData);
              pendingData = "";
              if (terminalReceived) break;
            }
            continue;
          }

          if (line.startsWith("data:")) {
            const payload = line.slice(5).trim();
            pendingData = pendingData
              ? pendingData + payload
              : payload;
          }
          // "event:" / "id:" / "retry:" 行忽略(不需要)
          if (line.startsWith("event:")) {
            const evtName = line.slice(6).trim();
            if (evtName === "ping") {
              // ping 不带 data, 单行就是一个事件, 清 pending
              pendingData = "";
            }
          }
        }
        if (terminalReceived) break;
      }

      // 流正常结束(非 terminal) → 重连
      if (!stopped && !terminalReceived) {
        triggerReconnect();
      }
    } catch (err) {
      if (stopped || terminalReceived) return;
      // AbortError 不算失败
      if (err instanceof DOMException && err.name === "AbortError") return;
      triggerReconnect();
    }
  }

  function triggerReconnect() {
    if (stopped || terminalReceived) return;
    consecutiveFailures += 1;
    if (consecutiveFailures > MAX_CONSECUTIVE_FAILURES) {
      stopped = true;
      onError?.(new Error("连接持续不稳定，会议可能仍在后台运行。请刷新页面。"));
      return;
    }
    const delay = Math.min(3000, 800 * 1.3 ** Math.min(consecutiveFailures, 8));
    reconnectTimer = setTimeout(connect, delay);
  }

  // 启动连接
  connect();

  return () => {
    stopped = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    abortController?.abort();
  };
}
