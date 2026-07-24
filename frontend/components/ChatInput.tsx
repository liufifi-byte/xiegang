"use client";

import { useState, useRef, KeyboardEvent } from "react";

interface Props {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

/** 朋友版会议输入区。回车发送，Shift+回车换行。 */
export function ChatInput({
  onSubmit,
  disabled,
  placeholder = "补充发言...",
}: Props) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setText("");
    if (ref.current) ref.current.style.height = "auto";
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  const handleInput = () => {
    if (!ref.current) return;
    ref.current.style.height = "auto";
    ref.current.style.height = `${Math.min(ref.current.scrollHeight, 120)}px`;
  };

  return (
    <div className="border-t border-[#e8e3e5] bg-white px-3 py-3 sm:px-5">
      <div className="rounded-xl border border-[#e9ded8] bg-[#fffdfb] p-2 focus-within:border-[#4b1c62] focus-within:ring-4 focus-within:ring-[#4b1c62]/10">
        <textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={disabled}
          className="min-h-[52px] w-full resize-none border-0 bg-transparent px-2 py-1.5 text-sm leading-relaxed text-[#251536] placeholder:text-[#aaa0a6] focus:outline-none disabled:opacity-50"
          placeholder={placeholder}
        />
        <div className="mt-1 flex items-center justify-between gap-3 px-1">
          <span className="hidden text-[11px] text-[#aaa0a7] sm:block">
            Enter 发送 · Shift + Enter 换行
          </span>
          <button
            onClick={submit}
            disabled={!text.trim() || disabled}
            className="ml-auto min-h-9 rounded-lg bg-[#4b1c62] px-4 text-xs font-bold text-white transition hover:bg-[#68227d] disabled:cursor-not-allowed disabled:opacity-30"
            aria-label="发送"
          >
            发送 ↑
          </button>
        </div>
      </div>
    </div>
  );
}
