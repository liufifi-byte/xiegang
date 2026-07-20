import type { ParliamentSummary, TranscriptEntry } from "./types";

export interface ParliamentStreamEvent {
  type:
    | "parliament_started"
    | "node_complete"
    | "parliament_done"
    | "cancelled"
    | "error";
  parliament_id?: string;
  topic?: string;
  node?: string;
  error?: string;
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
  const eventSource = new EventSource(url);

  eventSource.onmessage = (e) => {
    try {
      const event: ParliamentStreamEvent = JSON.parse(e.data);
      onEvent(event);
      if (
        event.type === "parliament_done" ||
        event.type === "cancelled" ||
        event.type === "error"
      ) {
        eventSource.close();
      }
    } catch (err) {
      onError?.(err as Error);
    }
  };

  eventSource.onerror = () => {
    onError?.(new Error("SSE 连接出错"));
    eventSource.close();
  };

  return () => eventSource.close();
}

