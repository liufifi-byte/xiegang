import { getDeviceId } from "./device";
import type { ClarificationQuestion } from "./types";

// 生产/本地都先指向 origin root（不带 /api 后缀）; 调用方拼接 /api/parliament/*。
// 浏览器直连后端域名(绕开 Vercel 边缘到国内源站的国际不稳定链路);
// 本地开发走同源 ''(空字符串) → 由 next.config.js 的 rewrites 反代到 127.0.0.1:8001。
const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "";

/** P0-S1.1: 构造统一 headers 含 X-Device-Id */
function buildHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  const deviceId = getDeviceId();
  if (deviceId) h["X-Device-Id"] = deviceId;
  return h;
}

export interface CreateParliamentResponse {
  parliament_id: string;
  stream_url: string;
  device_id?: string;
}

export interface ExpertSelectionPreviewResponse {
  recommended_expert_ids: string[];
  coverage: string[];
  reasons: Record<string, string>;
  selection_source: "category_rule" | "topic_rule" | string;
}

export class RateLimitError extends Error {
  retryAfter: number;
  constructor(message: string, retryAfter: number) {
    super(message);
    this.name = "RateLimitError";
    this.retryAfter = retryAfter;
  }
}

export async function createParliament(
  topic: string,
  topicCategory?: string,
  userBackground?: string,
  userId?: string,
  selectedExpertIds?: string[]
): Promise<CreateParliamentResponse> {
  const res = await fetch(`${API_BASE}/api/parliament`, {
    method: "POST",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      topic,
      topic_category: topicCategory || null,
      user_background: userBackground || null,
      user_id: userId || null,
      selected_expert_ids: selectedExpertIds || null,
    }),
  });
  if (res.status === 429) {
    const data = await res.json().catch(() => ({ detail: "请求过于频繁", retry_after_seconds: 60 }));
    throw new RateLimitError(
      data.detail || "请求过于频繁",
      data.retry_after_seconds || 60
    );
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`createParliament ${res.status}: ${text}`);
  }
  return res.json();
}

export async function previewExpertSelection(
  topic: string,
  topicCategory?: string,
  userBackground?: string
): Promise<ExpertSelectionPreviewResponse> {
  const res = await fetch(`${API_BASE}/api/expert-selection/preview`, {
    method: "POST",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      topic,
      topic_category: topicCategory || null,
      user_background: userBackground || null,
    }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`previewExpertSelection ${res.status}: ${text}`);
  }
  return res.json();
}

/** V1-S3: 用户在群里插话，真写入后端 runtime_context。 */
export async function interruptParliament(
  parliamentId: string,
  content: string
): Promise<{ ok: boolean; queued_at: boolean }> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  const res = await fetch(
    `${base}/api/parliament/${parliamentId}/interrupt`,
    {
      method: "POST",
      headers: buildHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ content }),
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`interruptParliament ${res.status}: ${text}`);
  }
  return res.json();
}

export async function submitClarification(
  parliamentId: string,
  action: "answer" | "skip",
  answers: Array<{ question_id: string; value: string }> = []
): Promise<{
  ok: boolean;
  clarification_status: "answered" | "skipped";
  already_submitted: boolean;
}> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  const res = await fetch(
    `${base}/api/parliament/${parliamentId}/clarification`,
    {
      method: "POST",
      headers: buildHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ action, answers }),
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`submitClarification ${res.status}: ${text}`);
  }
  return res.json();
}

/** V1-S4: 取消议会（V2-S2 硬中断路径）。 */
export async function cancelParliament(
  parliamentId: string
): Promise<{ ok: boolean; message: string }> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "";
  const res = await fetch(
    `${base}/api/parliament/${parliamentId}/cancel`,
    { method: "POST", headers: buildHeaders() }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`cancelParliament ${res.status}: ${text}`);
  }
  return res.json();
}
export async function getParliament(parliamentId: string) {
  const res = await fetch(`${API_BASE}/api/parliament/${parliamentId}`);
  if (!res.ok) throw new Error(`getParliament ${res.status}`);
  return res.json();
}

export async function listParliaments() {
  const res = await fetch(`${API_BASE}/api/parliament/`);
  if (!res.ok) throw new Error(`listParliaments ${res.status}`);
  return res.json();
}

export async function heartbeatParliament(
  parliamentId: string,
  keepRunning = false
): Promise<{ ok: boolean }> {
  const res = await fetch(`${API_BASE}/api/parliament/${parliamentId}/heartbeat`, {
    method: "POST",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ keep_running: keepRunning }),
  });
  if (!res.ok) throw new Error(`heartbeatParliament ${res.status}`);
  return res.json();
}

export interface CurrentParliament {
  id: string;
  topic: string;
  status: "queued" | "running" | "awaiting_input" | "done";
  current_phase?: string;
  created_at: string;
  members_state?: Record<string, unknown>;
  clarification_status?: string;
  clarification_questions?: ClarificationQuestion[];
}

export async function getCurrentParliament(): Promise<CurrentParliament | null> {
  const res = await fetch(`${API_BASE}/api/parliament/active/current`, {
    headers: buildHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`getCurrentParliament ${res.status}`);
  const data = await res.json();
  return data.item || null;
}
