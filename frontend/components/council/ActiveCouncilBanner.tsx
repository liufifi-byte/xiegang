"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  CurrentParliament,
  getCurrentParliament,
  heartbeatParliament,
} from "@/lib/api";

const PHASE_LABELS: Record<string, string> = {
  queued: "等待分析服务",
  claimed: "正在理解问题",
  researching: "专家正在并行调研",
  cross_examining: "专家正在交叉回应",
  summarizing: "主席正在整理结论",
  done: "分析报告已生成",
};

export function ActiveCouncilBanner() {
  const router = useRouter();
  const [council, setCouncil] = useState<CurrentParliament | null>(null);

  useEffect(() => {
    let active = true;
    getCurrentParliament()
      .then((item) => {
        if (active) setCouncil(item);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!council || council.status === "done") return;
    heartbeatParliament(council.id, true).catch(() => {});
    const timer = window.setInterval(
      () => heartbeatParliament(council.id, true).catch(() => {}),
      10_000
    );
    return () => window.clearInterval(timer);
  }, [council]);

  if (!council) return null;
  const doneCount = Object.keys(council.members_state || {}).length;
  const completed = council.status === "done";

  return (
    <button
      type="button"
      onClick={() =>
        router.push(
          completed ? `/summary/${council.id}` : `/parliament/${council.id}`
        )
      }
      className="mx-auto mb-5 flex w-[calc(100%-2.5rem)] max-w-3xl items-center justify-between gap-4 rounded-2xl border border-[#eadfd9] bg-white px-4 py-3 text-left shadow-sm transition hover:-translate-y-0.5 hover:shadow-md"
    >
      <span className="min-w-0">
        <span className="block text-[10px] font-extrabold tracking-[0.12em] text-[#ec403d]">
          {completed ? "最近完成" : "正在进行的会议"}
        </span>
        <strong className="mt-1 block truncate text-sm text-[#251536]">
          {council.topic}
        </strong>
        <small className="mt-1 block text-xs text-[#817681]">
          {PHASE_LABELS[council.current_phase || council.status] ||
            "正在恢复会议"}
          {!completed && ` · ${doneCount} 位专家已完成`}
        </small>
      </span>
      <span className="shrink-0 rounded-xl bg-[#4b1c62] px-4 py-2 text-xs font-bold text-white">
        {completed ? "查看结果" : "继续进入"}
      </span>
    </button>
  );
}
