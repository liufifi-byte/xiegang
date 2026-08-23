"use client";

import { getMember, MemberId, MemberStatus } from "@/lib/types";

const STATUS_LABEL: Record<MemberStatus, string> = {
  idle: "等待中",
  thinking: "正在判断",
  using_tool: "正在查证",
  organizing: "整理证据",
  speaking: "正在发言",
  done: "已形成判断",
  silent: "本轮未发言",
  degraded: "信息有限",
  error: "本轮未完成",
};

export function ExpertSeatPanel({
  expertIds,
  statuses,
  activeMember,
  onSelect,
  compact = false,
  researchPhase = false,
  researchAllDone = false,
}: {
  expertIds: MemberId[];
  statuses: Partial<Record<MemberId, MemberStatus>>;
  activeMember: MemberId | null;
  onSelect: (id: MemberId) => void;
  compact?: boolean;
  /** 调研阶段：席位文案统一为「调研阶段」，避免微观状态术语困扰用户 */
  researchPhase?: boolean;
  /** 调研全员完成：「已调研完成」（完整句"即将开始辩论"由阶段横幅承担） */
  researchAllDone?: boolean;
}) {
  const seatLabel = (status: MemberStatus) =>
    researchAllDone ? "已调研完成" : researchPhase ? "调研阶段" : STATUS_LABEL[status];

  if (compact) {
    return (
      <div className="flex gap-2 overflow-x-auto border-b border-[#e8e3e5] bg-white px-3 py-2 lg:hidden">
        {expertIds.map((id) => {
          const expert = getMember(id);
          if (!expert) return null;
          const status = statuses[id] || "idle";
          return (
            <button
              key={id}
              type="button"
              onClick={() => onSelect(id)}
              className={`flex shrink-0 items-center gap-2 rounded-full border px-2 py-1.5 ${
                activeMember === id
                  ? "border-[#4b1c62] bg-[#f3e9f3]"
                  : "border-[#e9ded8] bg-white"
              }`}
            >
              <span
                className="grid h-7 w-7 place-items-center rounded-lg text-xs font-bold text-white"
                style={{ backgroundColor: expert.color }}
              >
                {expert.avatar_char}
              </span>
              <span className="text-xs font-bold text-[#4b3e4d]">
                {expert.display_name}
              </span>
              <span className="text-[10px] text-[#978994]">
                {seatLabel(status)}
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <aside className="hidden overflow-y-auto border-l border-[#e0dce0] bg-white px-4 py-6 lg:block">
      <p className="text-[11px] font-extrabold tracking-[0.12em] text-[#ec403d]">
        专家席
      </p>
      <div className="mt-4 space-y-4">
        {expertIds.map((id) => {
          const expert = getMember(id);
          if (!expert) return null;
          const status = statuses[id] || "idle";
          const active = [
            "thinking",
            "using_tool",
            "organizing",
            "speaking",
          ].includes(status);
          return (
            <button
              key={id}
              type="button"
              onClick={() => onSelect(id)}
              className={`flex w-full items-center gap-2.5 rounded-xl p-1.5 text-left transition ${
                activeMember === id ? "bg-[#f3e9f3]" : "hover:bg-[#faf6f2]"
              }`}
            >
              <span className="relative">
                <span
                  className={`grid h-9 w-9 place-items-center rounded-[10px] text-xs font-bold text-white ${
                    active ? "animate-pulse-ring" : ""
                  }`}
                  style={{ backgroundColor: expert.color }}
                >
                  {expert.avatar_char}
                </span>
                {(status === "done" || status === "degraded") && (
                  <span className="absolute -bottom-1 -right-1 grid h-4 w-4 place-items-center rounded-full bg-[#4b1c62] text-[9px] text-white">
                    ✓
                  </span>
                )}
              </span>
              <span className="min-w-0">
                <strong className="block truncate text-[13px] text-[#2d2830]">
                  {expert.display_name}
                </strong>
                <small
                  className={`mt-0.5 block text-[11px] ${
                    active ? "font-bold text-[#b34b58]" : "text-[#968c95]"
                  }`}
                >
                  {seatLabel(status)}
                </small>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
