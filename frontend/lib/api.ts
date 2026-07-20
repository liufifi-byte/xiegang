import { getDeviceId } from "./device";

const API_BASE = "/api";

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
  userId?: string
): Promise<CreateParliamentResponse> {
  const res = await fetch(`${API_BASE}/parliament`, {
    method: "POST",
    headers: buildHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      topic,
      topic_category: topicCategory || null,
      user_background: userBackground || null,
      user_id: userId || null,
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

/** V1-S3: 用户在群里插话，真写入后端 runtime_context。 */
export async function interruptParliament(
  parliamentId: string,
  content: string
): Promise<{ ok: boolean; queued_at: boolean }> {
  const base = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
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

/** V1-S4: 取消议会（V2-S2 硬中断路径）。 */
export async function cancelParliament(
  parliamentId: string
): Promise<{ ok: boolean; message: string }> {
  const base = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
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
  const res = await fetch(`${API_BASE}/parliament/${parliamentId}`);
  if (!res.ok) throw new Error(`getParliament ${res.status}`);
  return res.json();
}

export async function listParliaments() {
  const res = await fetch(`${API_BASE}/parliament/`);
  if (!res.ok) throw new Error(`listParliaments ${res.status}`);
  return res.json();
}
