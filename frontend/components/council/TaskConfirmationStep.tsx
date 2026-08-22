"use client";

import React from "react";
import type { DecisionPathPreview } from "@/lib/types";

interface Props {
  preview: DecisionPathPreview;
  editingBrief: boolean;
  briefFeedback: string;
  error: string | null;
  /** 存在尚未应用的非空修改内容（父级计算：editingBrief && briefFeedback 非空） */
  hasPendingBriefFeedback: boolean;
  /** 正在重新梳理或正在创建会议 */
  busy: boolean;
  onEditingChange: (open: boolean) => void;
  onFeedbackChange: (value: string) => void;
  onRegenerate: () => void;
  onContinue: () => void;
  onBack: () => void;
  maxFeedback: number;
}

export function TaskConfirmationStep({
  preview,
  editingBrief,
  briefFeedback,
  error,
  hasPendingBriefFeedback,
  busy,
  onEditingChange,
  onFeedbackChange,
  onRegenerate,
  onContinue,
  onBack,
  maxFeedback,
}: Props) {
  const continueDisabled = busy || hasPendingBriefFeedback;

  return (
    <>
      {/* 1. 两步进度 */}
      <div className="mb-4 flex items-center gap-2 text-[11px] font-bold text-[#172d4d]">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#172d4d] text-white">1</span>
        <span>确认任务</span>
        <span className="h-px flex-1 bg-[#d9e1ea]" />
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#e8edf3] text-[#718096]">2</span>
        <span className="text-[#8290a3]">补充信息</span>
      </div>

      {/* 2. 决策目标卡片 */}
      <div className="rounded-xl border border-[#dbe5ef] bg-[#f5f8fc] px-4 py-3.5">
        <p className="mb-1 text-[11px] font-bold text-[#607692]">决策目标</p>
        <p className="text-[15px] leading-6 text-[#1d3049]">{preview.brief.decision_goal}</p>
        {preview.fallback_used && (
          <p className="mt-2 text-xs leading-5 text-[#8a6b25]">
            当前内容根据已有信息生成，你可以在确认前修改。
          </p>
        )}
      </div>

      {/* 3. 比较方案 */}
      <section className="mt-4">
        <p className="mb-1.5 text-[11px] font-bold text-[#607692]">比较方案</p>
        <div className="flex flex-wrap gap-2">
          {preview.brief.decision_options.map((option) => (
            <span
              key={option}
              className="rounded-full border border-[#d7e0ea] bg-white px-3 py-1.5 text-xs text-[#3d526f]"
            >
              {option}
            </span>
          ))}
        </div>
      </section>

      {/* 4. 重点分析 / 5. 最终交付（单列全量，不再双列） */}
      <SummaryBlock
        title="重点分析"
        items={preview.brief.analysis_dimensions}
        tone="primary"
      />
      <SummaryBlock
        title="最终交付"
        items={preview.brief.final_deliverables}
        tone="secondary"
      />

      {/* 6. 修改任务理解（轻量折叠区） */}
      <div className="mt-3">
        <button
          type="button"
          onClick={() => onEditingChange(!editingBrief)}
          aria-expanded={editingBrief}
          className="text-xs text-[#657892] underline-offset-2 hover:underline"
        >
          {editingBrief ? "收起修改" : "信息不准确？修改任务理解"}
        </button>
        <BriefEditPanel
          open={editingBrief}
          value={briefFeedback}
          maxLength={maxFeedback}
          onChange={onFeedbackChange}
          onRegenerate={onRegenerate}
          onClose={() => onEditingChange(false)}
        />
      </div>

      {error && (
        <p className="mt-4 rounded-lg bg-[#fff0ef] px-3 py-2 text-xs leading-5 text-[#a43f48]" role="alert">
          {error}
        </p>
      )}

      {/* 7. 底部操作区 */}
      <div className="mt-5 flex flex-col-reverse gap-3 border-t border-[#e6eaf0] pt-4 sm:flex-row sm:items-center sm:justify-between">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg px-2 py-2 text-sm text-[#657892] hover:bg-[#f3f6f9]"
        >
          返回修改决策
        </button>
        <button
          type="button"
          disabled={continueDisabled}
          onClick={onContinue}
          className="rounded-lg bg-[#172d4d] px-5 py-3 text-sm font-bold text-white shadow-sm transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:bg-[#c9d2dd] disabled:text-[#5d6c7e] disabled:shadow-none"
        >
          确认，继续补充信息 →
        </button>
      </div>
    </>
  );
}

/** 单列摘要块：每项一个轻量文字标签（§6.4/§6.5），禁止双列。 */
function SummaryBlock({
  title,
  items,
  tone = "primary",
}: {
  title: string;
  items: string[];
  tone?: "primary" | "secondary";
}) {
  const dot = tone === "primary" ? "bg-[#386998]" : "bg-[#8fb0d4]";
  return (
    <section className="mt-4">
      <p className="mb-1.5 text-[11px] font-bold text-[#607692]">{title}</p>
      <div className="flex flex-wrap gap-x-3 gap-y-2">
        {items.map((item) => (
          <span key={item} className="flex items-center gap-2 text-sm leading-5 text-[#3f5065]">
            <span className={`h-1 w-1 shrink-0 rounded-full ${dot}`} aria-hidden="true" />
            {item}
          </span>
        ))}
      </div>
    </section>
  );
}

/** 修改任务理解面板（§6.6）：默认折叠，浅灰背景，不使用重边框卡片。 */
function BriefEditPanel({
  open,
  value,
  maxLength,
  onChange,
  onRegenerate,
  onClose,
}: {
  open: boolean;
  value: string;
  maxLength: number;
  onChange: (value: string) => void;
  onRegenerate: () => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="mt-3 rounded-xl bg-[#f6f8fb] p-3">
      <label htmlFor="brief-feedback" className="mb-1 block text-xs text-[#60728a]">
        需要修正什么？
      </label>
      <textarea
        id="brief-feedback"
        className="w-full resize-none rounded-lg border border-[#d7e0ea] bg-white px-3 py-2 text-sm text-[#263b57] outline-none focus:border-[#386998] focus:ring-2 focus:ring-[#386998]/15"
        rows={3}
        maxLength={maxLength}
        placeholder="例如：我更关注长期成长，不希望把短期薪资作为首要标准。"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[10px] text-[#8290a3]" aria-live="polite">
          {value.length}/{maxLength}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-2 text-xs text-[#60728a] hover:bg-[#eef2f7]"
          >
            收起
          </button>
          <button
            type="button"
            disabled={!value.trim()}
            onClick={onRegenerate}
            className="rounded-lg bg-[#172d4d] px-3 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:bg-[#c9d2dd] disabled:text-[#5d6c7e]"
          >
            重新梳理任务
          </button>
        </div>
      </div>
    </div>
  );
}
