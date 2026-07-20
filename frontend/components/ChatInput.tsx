"use client";

import { useState, useRef, KeyboardEvent } from "react";

interface Props {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

/** 底部插话输入框（药丸形）。回车发送，Shift+回车换行。 */
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
    <div className="sticky bottom-0 left-0 right-0 bg-bg-primary/95 backdrop-blur border-t border-edge">
      <div className="max-w-3xl mx-auto px-4 py-3 flex gap-2 items-end">
        <textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          rows={1}
          disabled={disabled}
          className="flex-1 px-4 py-2.5 bg-bg-surface border border-edge rounded-3xl text-ink placeholder:text-ink-3 focus:outline-none focus:border-brand focus:ring-1 focus:ring-brand/30 resize-none text-sm leading-relaxed disabled:opacity-50"
          placeholder={placeholder}
        />
        <button
          onClick={submit}
          disabled={!text.trim() || disabled}
          className="flex-shrink-0 w-10 h-10 rounded-full bg-brand text-white flex items-center justify-center disabled:opacity-30 disabled:cursor-not-allowed hover:bg-brand-hover transition"
          aria-label="发送"
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
            <path
              d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}
