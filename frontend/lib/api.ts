const API_BASE = "/api";

export interface CreateParliamentResponse {
  parliament_id: string;
  stream_url: string;
}

export async function createParliament(
  topic: string,
  topicCategory?: string,
  userBackground?: string,
  userId?: string
): Promise<CreateParliamentResponse> {
  const res = await fetch(`${API_BASE}/parliament`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      topic,
      topic_category: topicCategory || null,
      user_background: userBackground || null,
      user_id: userId || null,
    }),
  });
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
  // 直连后端（与 SSE 同源）
  const base = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
  const res = await fetch(
    `${base}/api/parliament/${parliamentId}/interrupt`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`interruptParliament ${res.status}: ${text}`);
  }
  return res.json();
}

/** V1-S4: 取消议会（关口套到 stream.py 的 cancel 检查位）。 */
export async function cancelParliament(
  parliamentId: string
): Promise<{ ok: boolean; message: string }> {
  const base = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8001";
  const res = await fetch(
    `${base}/api/parliament/${parliamentId}/cancel`,
    { method: "POST" }
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
