"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { SummaryPanel } from "@/components/SummaryPanel";
import { ParliamentSummary, TranscriptEntry } from "@/lib/types";

/**
 * 独立 summary 路由页面：从数据库拉议会结果并渲染纪要。
 *
 * - 调 GET /api/parliament/{id}
 * - 加载态：spinner
 * - 404：友好的"议会不存在"页
 * - 议会未完成：提示稍后再来
 *
 * 这是 V1-S1 修复 #1（之前议会 done 时内联渲染，刷新即丢失）。
 */
export default function SummaryPage() {
  const params = useParams();
  const router = useRouter();
  const parliamentId = params.id as string;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<ParliamentSummary | null>(null);
  const [topic, setTopic] = useState("");
  const [duration, setDuration] = useState<number | undefined>(undefined);
  const [userMessages, setUserMessages] = useState<TranscriptEntry[]>([]);

  useEffect(() => {
    if (!parliamentId) return;
    void loadParliament();
  }, [parliamentId]);

  async function loadParliament() {
    setLoading(true);
    setError(null);
    try {
      // 浏览器直连后端域名(绕开 Vercel 边缘到国内源站的国际不稳定链路)
      // 本地开发走同源 ''(空字符串) → 由 next.config.js 的 rewrites 反代
      const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "";
      const res = await fetch(
        `${apiBase}/api/parliament/${parliamentId}?include_agent_traces=false`,
        { method: "POST" }
      );
      if (res.status === 404) {
        setError("议会不存在或已过期");
        setLoading(false);
        return;
      }
      if (!res.ok) {
        setError(`加载失败 (${res.status})`);
        setLoading(false);
        return;
      }
      const data = await res.json();

      // 议会还没跑完
      if (data.status !== "done" || !data.summary) {
        setError("议会尚未完成，请稍后再来");
        setLoading(false);
        return;
      }

      setSummary(data.summary as ParliamentSummary);
      setTopic(data.topic);
      if (data.finished_at && data.created_at) {
        const ms =
          new Date(data.finished_at).getTime() -
          new Date(data.created_at).getTime();
        setDuration(Math.round(ms / 1000));
      }

      // V1-S3: 从 transcript 里抽 type=user_interrupt 条目（后端 archive 已存）
      const transcript = (data.transcript || []) as TranscriptEntry[];
      const interruptEntries = transcript.filter(
        (e) => e.type === "user_interrupt"
      );
      if (interruptEntries.length > 0) {
        setUserMessages(interruptEntries);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  // 加载中
  if (loading) {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block w-6 h-6 border-2 border-edge border-t-brand rounded-full animate-spin mb-3" />
          <div className="text-sm text-ink-3">正在加载议会纪要...</div>
        </div>
      </main>
    );
  }

  // 错误 / 不存在
  if (error || !summary) {
    return (
      <main className="min-h-screen flex items-center justify-center px-6">
        <div className="text-center max-w-md">
          <div className="text-2xl text-ink mb-3">议会纪要无法加载</div>
          <div className="text-sm text-ink-3 mb-6">{error}</div>
          <button
            onClick={() => router.push("/")}
            className="px-5 py-2 text-sm border border-edge-strong text-ink-2 hover:border-brand hover:text-brand transition rounded"
          >
            ← 返回首页
          </button>
        </div>
      </main>
    );
  }

  // 渲染纪要
  return (
    <SummaryPanel
      summary={summary}
      topic={topic}
      durationSeconds={duration}
      userMessages={userMessages}
    />
  );
}
