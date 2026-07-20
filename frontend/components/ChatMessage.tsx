"use client";

import { useState } from "react";
import { getMember, TranscriptEntry } from "@/lib/types";

interface Props {
  entry: TranscriptEntry;
}

const STANCE_BADGE: Record<string, { label: string; bg: string; text: string }> = {
  for:     { label: "支持", bg: "bg-stance-forBg",     text: "text-stance-forText" },
  against: { label: "反对", bg: "bg-stance-againstBg", text: "text-stance-againstText" },
  neutral: { label: "中立", bg: "bg-stance-neutralBg", text: "text-stance-neutralText" },
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
        {/* 名字 + 时间 */}
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-sm font-medium" style={{ color }}>
            {member.display_name}
          </span>
          {entry.round !== undefined && (
            <span className="text-xs text-ink-subtle">
              R{entry.round}
            </span>
          )}
          <span className="text-xs text-ink-subtle">{time}</span>
        </div>

        {/* 气泡 */}
        <div
          className="inline-block max-w-[85%] bg-white border border-edge rounded-md rounded-tl-none px-3.5 py-2.5"
          style={{ borderLeft: `3px solid ${color}` }}
        >
          <div
            className={`text-sm leading-relaxed whitespace-pre-wrap break-words ${
              isError ? "text-stance-againstText italic" : "text-ink"
            }`}
          >
            {entry.content}
          </div>

          {/* 展开证据按钮 */}
          {evidenceLen > 0 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="mt-2 text-xs text-ink-muted hover:text-brand transition flex items-center gap-1"
            >
              {expanded ? "▾ 收起证据" : `▸ 展开 ${evidenceLen} 条证据`}
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

        {/* meta：立场 + 置信度 */}
        {(stance || entry.confidence !== undefined) && (
          <div className="flex items-center gap-2 mt-1.5 text-xs">
            {stance && (
              <span
                className={`px-1.5 py-0.5 rounded ${stance.bg} ${stance.text}`}
              >
                {stance.label}
              </span>
            )}
            {entry.confidence !== undefined && (
              <span className="text-ink-subtle">
                置信 {(entry.confidence * 100).toFixed(0)}%
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function formatTime(round?: number): string {
  // MVP：用 round 当假时间戳。后端 archive 真实时间。
  return round === 0 ? "调研阶段" : `第 ${round} 轮`;
}
