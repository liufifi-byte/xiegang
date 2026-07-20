"use client";

import { MEMBERS, MemberId, MemberStatus } from "@/lib/types";

interface Props {
  memberStatus: Record<MemberId, MemberStatus>;
  currentRound: number;
  maxRound: number;
  /** 已完成的议员数（用于显示进度） */
  doneCount?: number;
  activeMember: MemberId | null;
  onMemberClick: (id: MemberId) => void;
}

/**
 * 5 议员状态条：放在深苔绿顶栏下方。
 * 卡片白底 + 议员彩色头像 + 状态指示。
 */
export function MemberStatusBar({
  memberStatus,
  currentRound,
  maxRound,
  doneCount = 0,
  activeMember,
  onMemberClick,
}: Props) {
  return (
    <div className="bg-bg-chat border-b border-edge">
      <div className="max-w-3xl mx-auto px-4 py-3">
        {/* 进度提示 */}
        <div className="flex items-center justify-between mb-2.5">
          <span className="text-xs text-ink-2 tracking-wider">
            议员
          </span>
          <span className="text-xs text-ink-2">
            {currentRound === 0
              ? `${doneCount}/5 在调研`
              : currentRound >= maxRound
              ? `辩论完成`
              : `第 ${currentRound} / ${maxRound} 轮`}
          </span>
        </div>

        {/* 5 议员卡片 */}
        <div className="grid grid-cols-5 gap-1.5">
            {MEMBERS.map((m) => {
              const status = memberStatus[m.id];
              const isActive = activeMember === m.id;
              // V1-S4: tooltip 显示议员思维模型 + 标志句式
              const tooltip = `${m.role}派 · ${m.model}\n招牌问句：${m.signature_line}`;
              return (
                <button
                  key={m.id}
                  onClick={() => onMemberClick(m.id)}
                  title={tooltip}
                  className={`relative flex flex-col items-center gap-1.5 py-2 rounded-md border transition group ${
                    isActive
                      ? "border-brand bg-brand/5"
                      : "border-transparent hover:border-edge-strong hover:bg-bg-elev"
                  }`}
                >
                {/* 头像 */}
                <div className="relative">
                  <div
                    className={`w-9 h-9 rounded flex items-center justify-center text-white text-sm font-medium ${
                      status === "thinking" || status === "speaking"
                        ? "animate-pulse-ring"
                        : ""
                    } ${status === "idle" ? "opacity-50" : ""}`}
                    style={{ backgroundColor: m.color }}
                  >
                    {m.avatar_char}
                  </div>
                  {status === "done" && (
                    <div className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 bg-brand text-white rounded-full flex items-center justify-center text-[9px] font-bold border-2 border-bg-chat">
                      ✓
                    </div>
                  )}
                  {status === "error" && (
                    <div className="absolute -bottom-0.5 -right-0.5 w-3.5 h-3.5 bg-red-700 text-white rounded-full flex items-center justify-center text-[9px] font-bold border-2 border-bg-chat">
                      !
                    </div>
                  )}
                </div>

                {/* 名字 */}
                <span
                  className="text-[11px] font-medium leading-none"
                  style={{ color: m.color }}
                >
                  {m.display_name}
                </span>

                {/* 状态文字 */}
                <StatusText status={status} />
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function StatusText({ status }: { status: MemberStatus }) {
  const map: Record<MemberStatus, { label: string; cls: string }> = {
    idle:     { label: "待命",   cls: "text-ink-3" },
    thinking: { label: "调研中", cls: "text-gold-muted" },
    speaking: { label: "发言中", cls: "text-brand" },
    done:     { label: "已发言", cls: "text-ink-3" },
    error:    { label: "出错",   cls: "text-red-700" },
  };
  const { label, cls } = map[status];
  return <span className={`text-[10px] leading-none ${cls}`}>{label}</span>;
}
