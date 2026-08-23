"use client";

import { useState } from "react";
import { getMember, Stance, TranscriptEntry } from "@/lib/types";

interface Props {
  entry: TranscriptEntry;
}

/** V3 四值枚举；for/against 仅历史议会数据 */
const STANCE_BADGE: Record<Stance, { label: string; bg: string; text: string }> = {
  support: { label: "建议推进", bg: "bg-stance-forBg",     text: "text-stance-forText" },
  oppose:  { label: "不建议",   bg: "bg-stance-againstBg", text: "text-stance-againstText" },
  cautious:{ label: "谨慎推进", bg: "bg-stance-neutralBg", text: "text-stance-neutralText" },
  neutral: { label: "中立",     bg: "bg-stance-neutralBg", text: "text-stance-neutralText" },
  for:     { label: "支持",     bg: "bg-stance-forBg",     text: "text-stance-forText" },
  against: { label: "反对",     bg: "bg-stance-againstBg", text: "text-stance-againstText" },
};

export function ChatMessage({ entry }: Props) {
  const [expanded, setExpanded] = useState(false);

  // 系统消息：居中显示
  if (entry.speaker === "system" || entry.type === "election") {
    return (
      <div className="my-4 flex items-center gap-3 px-2">
        <span className="flex-1 h-px bg-edge" />
        <span className="text-xs text-ink-muted whitespace-nowrap">
          {entry.content}
        </span>
        <span className="flex-1 h-px bg-edge" />
      </div>
    );
  }

  const member = getMember(entry.speaker);
  if (!member) return null;

  const color = member.color;
  const stance = entry.stance ? STANCE_BADGE[entry.stance] : null;
  const isError = entry.type === "debate_error";
  // 降级发言：斜体淡化提示，但不红色（区别于 debate_error）
  const isDegraded = entry.type === "debate_degraded";
  const evidenceLen = entry.evidence?.length || 0;
  const time = entry.timestamp || formatTime(entry.round);

  return (
    <div className="flex gap-2.5 px-4 py-2 animate-slide-in">
      {/* 头像 */}
      <div
        className="flex-shrink-0 w-9 h-9 rounded-md flex items-center justify-center text-white text-sm font-medium"
        style={{ backgroundColor: color }}
      >
        {member.avatar_char}
      </div>

      {/* 气泡主体 */}
      <div className="flex-1 min-w-0">
        {/* 名字 + 时间（"调研阶段"/轮次只在这里出现一次，spec §14.2） */}
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-sm font-medium" style={{ color }}>
            {member.display_name}
          </span>
          <span className="text-xs text-ink-subtle">{time}</span>
        </div>

        {/* 气泡 */}
        <div
          className="inline-block max-w-[85%] bg-white border border-edge rounded-md rounded-tl-none px-3.5 py-2.5"
          style={{ borderLeft: `3px solid ${color}` }}
        >
          <div
            className={`text-sm leading-relaxed break-words ${
              isError
                ? "text-stance-againstText italic"
                : isDegraded
                ? "text-ink-muted italic"
                : "text-ink"
            }`}
          >
            {renderParagraphs(entry.content, color)}
          </div>

          {/* 展开证据按钮 */}
          {evidenceLen > 0 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="mt-2 text-xs text-ink-muted hover:text-brand transition flex items-center gap-1"
            >
              {expanded ? "▾ 收起分析依据" : `▸ 查看 ${evidenceLen} 项分析依据`}
            </button>
          )}

          {expanded && entry.evidence && (
            <ul className="mt-2 space-y-1.5 pt-2 border-t border-edge">
              {entry.evidence.map((e, i) => (
                <li
                  key={i}
                  className="text-xs text-ink-secondary leading-relaxed"
                >
                  <span className="text-ink-subtle mr-1">
                    [{e.strength}]
                  </span>
                  {e.claim}
                  {e.source && (
                    <span className="text-ink-subtle ml-1">
                      — {e.source}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* meta：只展示倾向徽章；basis_level 是内部质量信号，不再显示（spec §14.2） */}
        {stance && (
          <div className="flex items-center gap-2 mt-1.5 text-xs">
            <span
              className={`px-1.5 py-0.5 rounded ${stance.bg} ${stance.text}`}
            >
              {stance.label}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * V3 四段契约渲染：后端 content 是【】标题 + 空行分段的纯文本。
 * 按空行拆段，段首的【标题】用专家色加粗，其余原样展示（保留段内换行）。
 */
function renderParagraphs(content: string, color: string) {
  return content.split(/\n{2,}/).map((para, i) => {
    const match = para.match(/^(【[^】]+】)([\s\S]*)$/);
    if (!match) {
      return (
        <p key={i} className="whitespace-pre-wrap">
          {para}
        </p>
      );
    }
    return (
      <p key={i} className="whitespace-pre-wrap">
        <span className="font-semibold" style={{ color }}>
          {match[1]}
        </span>
        {match[2]}
      </p>
    );
  });
}

function formatTime(round?: number): string {
  return round === 0 ? "调研阶段" : `第 ${round} 轮`;
}
