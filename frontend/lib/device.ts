/**
 * V2 P0-S1.1: 匿名 device_id 用于限流。
 *
 * 首次访问生成 UUID，存 localStorage。
 * 所有 API 请求带 X-Device-Id header。
 * 后端用此 + IP 双轨限流。
 */

const STORAGE_KEY = "xiegang_device_id";

/**
 * 获取或生成 device_id（浏览器内唯一）。
 * 在 SSR 环境（无 window）返回空字符串。
 */
export function getDeviceId(): string {
  if (typeof window === "undefined") return "";
  let id = localStorage.getItem(STORAGE_KEY);
  if (!id) {
    id = generateUUID();
    localStorage.setItem(STORAGE_KEY, id);
  }
  return id;
}

/**
 * 4-bit UUID v4 实现（不依赖 crypto.randomUUID 因为旧浏览器可能没）。
 */
function generateUUID(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // fallback
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}
