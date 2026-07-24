"use client";

import { Member } from "@/lib/types";

interface Props {
  expert: Member;
  reason: string;
  selected: boolean;
  recommended: boolean;
  onToggle: () => void;
}

export function ExpertSelectionCard({
  expert,
  reason,
  selected,
  recommended,
  onToggle,
}: Props) {
  return (
    <article
      className={`relative min-h-[164px] rounded-2xl border bg-[#fffdfb] p-5 text-left transition ${
        selected
          ? "border-[#4b1c62] shadow-[inset_0_0_0_1px_#4b1c62,0_12px_30px_rgba(54,28,53,0.09)]"
          : "border-[#e9ded8] hover:-translate-y-0.5 hover:border-[#4b1c62]/45 hover:shadow-[0_12px_30px_rgba(54,28,53,0.09)]"
      }`}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        aria-label={`${selected ? "取消选择" : "选择"}讨论者：${expert.display_name}`}
        className="w-full text-left focus:outline-none"
      >
        <span
          className="mb-3.5 grid h-9 w-9 place-items-center rounded-xl bg-[#f3e9f3] text-base font-extrabold"
          style={{ color: expert.color }}
        >
          {expert.avatar_char}
        </span>
        <span className="flex items-center gap-2 pr-8">
          <span className="text-[17px] font-bold text-[#251536]">
            {expert.display_name}
          </span>
          {recommended && (
            <span className="rounded-full bg-[#fff0ed] px-2 py-0.5 text-[10px] font-bold text-[#a84a45]">
              推荐
            </span>
          )}
        </span>
        <span className="mt-2 block text-[13px] leading-6 text-[#70646e]">
          {reason}
        </span>
        <span className="mt-3 block text-xs font-medium text-[#a84a45]">
          方法：{expert.model}
        </span>
      </button>

      <span className="absolute bottom-4 right-4 inline-flex items-center gap-1.5 text-xs font-bold text-[#81747d]">
        <span
          className={`grid h-[18px] w-[18px] place-items-center rounded-[5px] border text-[11px] ${
            selected
              ? "border-[#4b1c62] bg-[#4b1c62] text-white"
              : "border-[#b6a8b0] bg-white text-transparent"
          }`}
        >
          ✓
        </span>
        {selected ? "已加入本次讨论" : "加入本次讨论"}
      </span>
    </article>
  );
}
