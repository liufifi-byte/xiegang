"use client";

import { TranscriptEntry } from "@/lib/types";

interface Props {
  entry: TranscriptEntry;
  /** 显示名，默认"我" */
  userName?: string;
}

/**
 * 用户在群聊里补充的发言气泡：右对齐 + 深青底白字。
 */
export function UserBubble({ entry, userName = "我" }: Props) {
  const time = entry.timestamp || "刚刚";

  return (
    <div className="flex gap-2.5 px-4 py-2 justify-end animate-slide-in">
      <div className="flex-1 min-w-0 flex flex-col items-end">
        {/* 名字 + 时间 */}
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-xs text-ink-3">{time}</span>
          <span className="text-sm font-medium text-me">
            {userName}
          </span>
        </div>

        {/* 气泡：右对齐 + 暖橙金句光环色（奇葩说系） */}
        <div className="inline-block max-w-[85%] bg-me text-white rounded-md rounded-tr-none px-3.5 py-2.5 shadow-sm">
          <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
            {entry.content}
          </div>
        </div>
      </div>

      {/* 头像 */}
      <div className="flex-shrink-0 w-9 h-9 rounded-md bg-me flex items-center justify-center text-white text-sm font-medium">
        {userName.charAt(0)}
      </div>
    </div>
  );
}
