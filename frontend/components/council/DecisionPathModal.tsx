"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  createParliamentV2,
  deleteDecisionPath,
  previewDecisionPath,
  DecisionPathPreviewRequest,
} from "@/lib/api";
import type { DecisionPathPreview } from "@/lib/types";
import { DecisionQuestionsStep } from "./DecisionQuestionsStep";

interface Props {
  open: boolean;
  topic: string;
  topicCategory: string | null;
  userBackground: string | null;
  selectedExpertIds: string[] | null;
  onClose: () => void;
  onParliamentCreated: (parliamentId: string) => void;
}

/**
 * 会前信息收集（2026-08-23 改版）：确认卡已移除——只在系统真的缺
 * 关键信息时才追问（后端最多 2 问），没有问题就直接开始会议。
 */
export function DecisionPathModal({ open, topic, topicCategory, userBackground, selectedExpertIds, onClose, onParliamentCreated }: Props) {
  const [status, setStatus] = useState<"idle" | "generating" | "ready" | "creating_meeting" | "create_failed">("idle");
  const [preview, setPreview] = useState<DecisionPathPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  const [questionIndex, setQuestionIndex] = useState(0);
  const [generationId, setGenerationId] = useState<string | null>(null);

  useEffect(() => {
    if (open) void generateInitial();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function generateInitial() {
    setStatus("generating");
    setError(null);
    setAnswers({});
    setQuestionIndex(0);
    setGenerationId(null);
    try {
      const request: DecisionPathPreviewRequest = { topic, topic_category: topicCategory, user_background: userBackground, selected_expert_ids: selectedExpertIds ?? undefined };
      const response = await previewDecisionPath(request);
      setPreview(response);
      setGenerationId(response.generation_id);
      // 没有需要追问的问题 → 直接开始会议，不弹任何确认界面
      if ((response.questions?.length ?? 0) === 0) {
        await createWithAnswers(response.generation_id, {});
        return;
      }
      setStatus("ready");
    } catch (cause) {
      setPreview(null);
      setError(cause instanceof Error ? cause.message : String(cause));
      setStatus("ready");
    }
  }

  function toAnswerArray(allAnswers: Record<string, string | string[]>) {
    return Object.entries(allAnswers).map(([question_id, value]) => ({ question_id, value }));
  }

  function setAnswer(questionId: string, value: string | string[]) {
    setAnswers((current) => ({ ...current, [questionId]: value }));
  }

  const allAnswered = useMemo(() => preview?.questions.every((question) => {
    const value = answers[question.id];
    return value !== undefined && value !== "" && (!Array.isArray(value) || value.length > 0);
  }) ?? false, [answers, preview]);

  async function createWithAnswers(genId: string, allAnswers: Record<string, string | string[]>) {
    if (status === "creating_meeting") return;
    setStatus("creating_meeting");
    setError(null);
    try {
      const idempotency = `cl_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
      const response = await createParliamentV2({ topic, topic_category: topicCategory, user_background: userBackground, selected_expert_ids: selectedExpertIds ?? undefined, idempotency_key: idempotency, decision_path_generation_id: genId, brief_confirmation: "confirmed", decision_path_answers: toAnswerArray(allAnswers) });
      onParliamentCreated(response.parliament_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setStatus("create_failed");
    }
  }

  function handleUnknown() {
    const question = preview?.questions[questionIndex];
    if (question) setAnswer(question.id, question.answer_type === "multi_choice" ? ["unknown"] : "unknown");
  }

  function handleClose() {
    if (generationId) deleteDecisionPath(generationId).catch(() => undefined);
    setStatus("idle");
    setPreview(null);
    setGenerationId(null);
    setError(null);
    onClose();
  }

  if (!open) return null;

  const generating = status === "generating";
  const creating = status === "creating_meeting";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#10233b]/45 px-3 py-4 backdrop-blur-sm sm:px-4" role="dialog" aria-modal="true" aria-labelledby="decision-path-title">
      <section className="flex max-h-[calc(100vh-2rem)] w-full max-w-[560px] flex-col overflow-hidden rounded-2xl border border-[#dce4ed] bg-[#fbfcfe] shadow-[0_24px_80px_rgba(16,35,59,0.22)]">
        <header className="shrink-0 border-b border-[#e6eaf0] bg-white px-5 py-4 sm:px-7">
          <p className="text-[10px] font-extrabold uppercase tracking-[0.16em] text-[#607692]">Decision room · 会前补充</p>
          <h2 id="decision-path-title" className="mt-1 text-lg font-bold text-[#172d4d] sm:text-xl">补充关键信息</h2>
          <p className="mt-1 text-xs text-[#718096]">只问会改变结论的问题</p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5 sm:px-7">
          {generating && (
            <div className="py-16 text-center"><div className="mx-auto h-9 w-9 animate-spin rounded-full border-2 border-[#dce4ed] border-t-[#172d4d]" /><p className="mt-4 text-sm text-[#718096]">正在梳理本次决策任务…</p></div>
          )}
          {creating && (
            <div className="py-16 text-center"><div className="mx-auto h-9 w-9 animate-spin rounded-full border-2 border-[#dce4ed] border-t-[#172d4d]" /><p className="mt-4 text-sm text-[#718096]">正在创建会议…</p></div>
          )}
          {preview && !generating && !creating && (status === "ready" || status === "create_failed") && (preview.questions?.length ?? 0) > 0 && (
            <DecisionQuestionsStep
              questions={preview.questions}
              index={questionIndex}
              answers={answers}
              error={error}
              onChange={setAnswer}
              onBack={questionIndex > 0 ? () => setQuestionIndex((current) => Math.max(0, current - 1)) : undefined}
              onNext={() => setQuestionIndex((current) => Math.min(current + 1, preview.questions.length - 1))}
              onUnknown={handleUnknown}
              onCreate={() => void createWithAnswers(generationId ?? preview.generation_id, answers)}
              onClose={handleClose}
              creating={creating}
            />
          )}
          {error && !preview && <div className="rounded-lg bg-[#fff0ef] px-3 py-2 text-xs leading-5 text-[#a43f48]">{error}<button type="button" onClick={() => void generateInitial()} className="ml-2 font-bold underline">重试</button></div>}
        </div>
      </section>
    </div>
  );
}
