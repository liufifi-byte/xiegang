"use client";

import clsx from "clsx";
import React from "react";
import type { DecisionPathPreview } from "@/lib/types";

interface Props {
  preview: DecisionPathPreview;
  agreed: "confirmed" | "adjust" | null;
  briefFeedback: string;
  error: string | null;
  onAgree: (value: "confirmed" | "adjust") => void;
  onFeedbackChange: (value: string) => void;
  onRegenerate: () => void;
  onContinue: () => void;
  onBack: () => void;
  maxFeedback: number;
}

export function TaskConfirmationStep({
  preview,
  agreed,
  briefFeedback,
  error,
  onAgree,
  onFeedbackChange,
  onRegenerate,
  onContinue,
  onBack,
  maxFeedback,
}: Props) {
  return (
    <>
      <div className="mb-5 flex items-center gap-2 text-[11px] font-bold text-[#172d4d]">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#172d4d] text-white">1</span>
        <span>确认任务</span>
        <span className="h-px flex-1 bg-[#d9e1ea]" />
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#e8edf3] text-[#718096]">2</span>
        <span className="text-[#8290a3]">补充信息</span>
      </div>

      <div className="rounded-xl border-l-4 border-[#386998] bg-[#f3f7fb] px-4 py-3.5">
        <p className="mb-1 text-[10px] font-extrabold uppercase tracking-[0.12em] text-[#607692]">决策目标</p>
        <p className="text-[15px] leading-7 text-[#1d3049]">{preview.brief.decision_goal}</p>
      </div>

      <SummarySection title="比较方案">
        <div className="flex flex-wrap gap-2">
          {preview.brief.decision_options.slice(0, 3).map((option) => (
            <span key={option} className="rounded-full border border-[#d7e0ea] bg-white px-3 py-1.5 text-xs text-[#3d526f]">
              {option}
            </span>
          ))}
        </div>
      </SummarySection>

      <div className="grid gap-3 sm:grid-cols-2">
        <SummarySection title="重点分析">
          <p className="text-sm leading-6 text-[#3f5065]">{preview.brief.analysis_dimensions.join("、")}</p>
        </SummarySection>
        <SummarySection title="最终交付">
          <p className="text-sm leading-6 text-[#3f5065]">
            {preview.brief.final_deliverables.slice(0, 2).join("、")}
          </p>
        </SummarySection>
      </div>

      {preview.fallback_used && (
        <p className="mt-4 rounded-lg bg-[#f7f1df] px-3 py-2 text-xs text-[#8b6a1b]">
          已根据场景生成基础简报，你仍可以调整理解。
        </p>
      )}

      {error && <p className="mt-4 rounded-lg bg-[#fff0ef] px-3 py-2 text-xs leading-5 text-[#a43f48]">{error}</p>}

      <div className="mt-6 border-t border-[#e6eaf0] pt-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm font-bold text-[#263b57]">这个任务理解准确吗？</p>
          <button type="button" onClick={() => onAgree("adjust")} className="text-xs text-[#657892] underline-offset-2 hover:underline">
            理解不准确？
          </button>
        </div>
        {agreed === "adjust" ? (
          <div className="mt-3 rounded-xl border border-[#dce4ed] bg-[#f8fafc] p-3">
            <label className="mb-1 block text-xs text-[#60728a]">告诉我们哪里需要修正</label>
            <textarea
              className="w-full resize-none rounded-lg border border-[#d7e0ea] bg-white px-3 py-2 text-sm text-[#263b57] outline-none focus:border-[#386998] focus:ring-2 focus:ring-[#386998]/15"
              rows={3}
              maxLength={maxFeedback}
              placeholder="例如：我是在比较市区老房和郊区新房。"
              value={briefFeedback}
              onChange={(event) => onFeedbackChange(event.target.value)}
            />
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[10px] text-[#8290a3]">{briefFeedback.length}/{maxFeedback}</span>
              <div className="flex items-center gap-2">
                <button type="button" onClick={() => onAgree("confirmed")} className="rounded-lg px-2 py-2 text-xs text-[#60728a]">理解正确</button>
                <button type="button" disabled={!briefFeedback.trim()} onClick={onRegenerate} className="rounded-lg bg-[#172d4d] px-3 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:opacity-40">重新梳理任务</button>
              </div>
            </div>
          </div>
        ) : (
          <button type="button" onClick={() => onAgree("confirmed")} className={clsx("mt-3 w-full rounded-lg border px-4 py-3 text-left text-sm transition", agreed === "confirmed" ? "border-[#172d4d] bg-[#edf3f9] font-bold text-[#172d4d]" : "border-[#d7e0ea] bg-white text-[#50627a] hover:border-[#7d9ab8]") }>
            {agreed === "confirmed" ? "✓ 理解正确，按这个目标继续" : "按这个目标继续"}
          </button>
        )}
      </div>

      <div className="mt-6 flex items-center justify-between gap-3 border-t border-[#e6eaf0] pt-4">
        <button type="button" onClick={onBack} className="rounded-lg px-2 py-2 text-sm text-[#657892] hover:bg-[#f3f6f9]">返回修改决策</button>
        <button type="button" disabled={agreed !== "confirmed"} onClick={onContinue} className="rounded-lg bg-[#172d4d] px-5 py-3 text-sm font-bold text-white shadow-sm transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:bg-[#d8dee7] disabled:text-[#8592a3] disabled:shadow-none">
          补充关键信息 →
        </button>
      </div>
    </>
  );
}

function SummarySection({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="mt-5"><p className="mb-1.5 text-[10px] font-extrabold uppercase tracking-[0.12em] text-[#718096]">{title}</p>{children}</section>;
}
