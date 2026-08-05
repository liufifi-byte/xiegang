"use client";

/**
 * 决策路径 Modal - PRD v1.0 §7-§9
 *
 * 用户体验流(PRD §7.1):
 * 1. 点开始讨论 → status="generating"
 * 2. previewDecisionPath() 成功 → status="ready" 显示弹窗
 * 3. 用户看简报 → 勾"同意" / "需要调整"
 * 4. 用户填完所有问题 + 已同意 → "确认并召开会议"可点
 * 5. confirm → status="creating_meeting" → createParliamentV2 → 跳议会页
 */

import { useEffect, useMemo, useState } from "react";
import clsx from "clsx";
import {
  previewDecisionPath,
  deleteDecisionPath,
  createParliamentV2,
  DecisionPathPreviewRequest,
} from "@/lib/api";
import type {
  DecisionAnswerItem,
  DecisionPathPreview,
  DecisionPathStatus,
  DecisionQuestion,
  DecisionQuestionOption,
  ExtractedFact,
} from "@/lib/types";

interface Props {
  open: boolean;
  topic: string;
  topicCategory: string | null;
  userBackground: string | null;
  selectedExpertIds: string[] | null;
  onClose: () => void;
  onParliamentCreated: (parliamentId: string) => void;
}

const MAX_BRIEF_FEEDBACK = 500;

/** PRD v1.1 §6.8: 默认公开承诺; 如果后端带了 agent_research_policy 就用服务端的 */
function researchPolicyMessage(preview: DecisionPathPreview): string {
  return (
    preview.agent_research_policy?.public_message
    || "公开信息将由专家在会议中自主判断并按需核实，实际搜索与计算会在发生后展示。"
  );
}

export function DecisionPathModal({
  open,
  topic,
  topicCategory,
  userBackground,
  selectedExpertIds,
  onClose,
  onParliamentCreated,
}: Props) {
  const [status, setStatus] = useState<DecisionPathStatus>("idle");
  const [preview, setPreview] = useState<DecisionPathPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 简报确认状态
  const [agreed, setAgreed] = useState<"confirmed" | "adjust" | null>(null);
  const [briefFeedback, setBriefFeedback] = useState("");
  // 答案
  const [answers, setAnswers] = useState<Record<string, string | string[]>>({});
  // 提取事实的本地修改
  const [factEdits, setFactEdits] = useState<Record<string, string>>({});
  const [editingFactId, setEditingFactId] = useState<string | null>(null);
  const [generationId, setGenerationId] = useState<string | null>(null);

  // 打开时触发生成
  useEffect(() => {
    if (!open) return;
    void generateInitial();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function generateInitial() {
    setStatus("generating");
    setError(null);
    setAgreed(null);
    setBriefFeedback("");
    setAnswers({});
    setFactEdits({});
    setGenerationId(null);
    setEditingFactId(null);
    try {
      const req: DecisionPathPreviewRequest = {
        topic,
        topic_category: topicCategory,
        user_background: userBackground,
        selected_expert_ids: selectedExpertIds ?? undefined,
      };
      const resp = await previewDecisionPath(req);
      setPreview(resp);
      setGenerationId(resp.generation_id);
      setStatus("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("ready");
      setPreview(null);
    }
  }

  async function regenerateWithFeedback() {
    if (!briefFeedback.trim() || !generationId) return;
    setStatus("revising");
    setError(null);
    setAgreed(null);
    try {
      const req: DecisionPathPreviewRequest = {
        topic,
        topic_category: topicCategory,
        user_background: userBackground,
        selected_expert_ids: selectedExpertIds ?? undefined,
        generation_id: generationId,
        brief_feedback: briefFeedback,
        existing_answers: toAnswerArray(),
      };
      const resp = await previewDecisionPath(req);
      setPreview(resp);
      setGenerationId(resp.generation_id);
      // PRD §8.3: 重新生成后稳定问题 id 的答案保留, 不稳定 id 的答案删掉
      const validIds = new Set(resp.questions.map((q) => q.id));
      setAnswers((prev) => {
        const filtered: Record<string, string | string[]> = {};
        for (const [k, v] of Object.entries(prev)) {
          if (validIds.has(k)) filtered[k] = v;
        }
        return filtered;
      });
      setStatus("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("ready");
    }
  }

  function setAnswer(questionId: string, value: string | string[]) {
    setAnswers((prev) => ({ ...prev, [questionId]: value }));
  }

  function toAnswerArray(): DecisionAnswerItem[] {
    return Object.entries(answers).map(([qid, val]) => ({
      question_id: qid,
      value: val,
    }));
  }

  // 是否所有问题都已答复(allow unknown as valid answer)
  const allAnswered = useMemo(() => {
    if (!preview) return false;
    return preview.questions.every((q) => {
      const a = answers[q.id];
      return a !== undefined && a !== null && (Array.isArray(a) ? a.length > 0 : a !== "");
    });
  }, [answers, preview]);

  const canConfirm = agreed === "confirmed" && allAnswered && status === "ready";

  async function handleConfirmCreate() {
    if (!canConfirm || !generationId) return;
    setStatus("creating_meeting");
    setError(null);
    try {
      const idempotency = `cl_${Date.now().toString(36)}_${Math.random()
        .toString(36)
        .slice(2, 8)}`;
      const resp = await createParliamentV2({
        topic,
        topic_category: topicCategory,
        user_background: userBackground,
        selected_expert_ids: selectedExpertIds ?? undefined,
        idempotency_key: idempotency,
        decision_path_generation_id: generationId,
        brief_confirmation: "confirmed",
        decision_path_answers: toAnswerArray(),
      });
      onParliamentCreated(resp.parliament_id);
    } catch (e) {
      // PRD §8.5/§16.3: 失败保留弹窗全部内容、可重试
      setError(e instanceof Error ? e.message : String(e));
      setStatus("create_failed");
    }
  }

  async function handleClose() {
    // 用户明确放弃时, 把草稿标 expired(避免后面 restore)
    if (generationId) {
      deleteDecisionPath(generationId).catch(() => {});
    }
    setStatus("idle");
    setPreview(null);
    setGenerationId(null);
    setError(null);
    onClose();
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 px-3 py-6 sm:px-4"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        // PRD §15.3: 不允许点击遮罩意外关闭
        if (e.target === e.currentTarget) return;
      }}
    >
      <div className="my-auto w-full max-w-2xl rounded-2xl bg-[#fffdfb] shadow-2xl">
        {/* Header */}
        <div className="border-b border-[#e9ded8] px-5 py-4 sm:px-7">
          <h2 className="font-serif text-lg font-bold text-[#251536] sm:text-xl">
            开始前，确认一下本次会议任务
          </h2>
          <p className="mt-0.5 text-xs text-[#998e96]">
            我们只询问会明显影响结论的信息
          </p>
        </div>

        {/* Body */}
        <div className="max-h-[60vh] overflow-y-auto px-5 py-5 sm:px-7">
          {status === "generating" && (
            <div className="py-12 text-center">
              <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-[#e9ded8] border-t-[#ec403d]" />
              <p className="mt-3 text-sm text-[#998e96]">正在生成决策路径…</p>
            </div>
          )}

          {status === "revising" && (
            <div className="py-12 text-center">
              <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-[#e9ded8] border-t-[#ec403d]" />
              <p className="mt-3 text-sm text-[#998e96]">根据反馈调整中…</p>
            </div>
          )}

          {status === "creating_meeting" && (
            <div className="py-12 text-center">
              <div className="mx-auto h-10 w-10 animate-spin rounded-full border-2 border-[#e9ded8] border-t-[#ec403d]" />
              <p className="mt-3 text-sm text-[#998e96]">正在创建会议…</p>
            </div>
          )}

          {error && (
            <div className="mb-4 rounded-xl bg-[#fdecec] px-4 py-3 text-sm text-[#c1272d]">
              {error}
            </div>
          )}

          {preview && (status === "ready" || status === "create_failed") && (
            <>
              {/* 任务简报 */}
              <BriefCard brief={preview.brief} preview={preview} />

              {preview.fallback_used && (
                <div className="mt-2 rounded-md bg-[#fff8e8] px-3 py-1.5 text-xs text-[#b58900]">
                  已根据场景生成基础简报，你仍可以修改。
                </div>
              )}

              {/* 同意 / 调整 */}
              <div className="mt-5">
                <p className="mb-2 text-sm font-bold text-[#4b3e4d]">
                  简报是否符合你的需求？
                </p>
                <div className="flex flex-wrap gap-2">
                  <ChoiceChip
                    active={agreed === "confirmed"}
                    onClick={() => setAgreed("confirmed")}
                  >
                    ☑ 同意，按以上目标分析
                  </ChoiceChip>
                  <ChoiceChip
                    active={agreed === "adjust"}
                    onClick={() => setAgreed("adjust")}
                  >
                    需要调整
                  </ChoiceChip>
                </div>
              </div>

              {agreed === "adjust" && (
                <div className="mt-3 rounded-xl border border-[#e9ded8] bg-[#fffaf6] p-3">
                  <label className="mb-1 block text-xs text-[#71646e]">
                    请告诉我们哪里理解得不准确
                  </label>
                  <textarea
                    className="w-full resize-none rounded-lg border border-[#e9ded8] bg-white px-3 py-2 text-sm"
                    rows={3}
                    maxLength={MAX_BRIEF_FEEDBACK}
                    placeholder="例如：我不是在比较租房和买房，而是在比较市区老房和郊区新房。"
                    value={briefFeedback}
                    onChange={(e) => setBriefFeedback(e.target.value)}
                  />
                  <div className="mt-2 flex items-center justify-between">
                    <span className="text-[10px] text-[#998e96]">
                      {briefFeedback.length}/{MAX_BRIEF_FEEDBACK}
                    </span>
                    <button
                      disabled={!briefFeedback.trim()}
                      onClick={regenerateWithFeedback}
                      className="rounded-md bg-[#4b1c62] px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-40"
                    >
                      根据补充重新生成
                    </button>
                  </div>
                </div>
              )}

              {/* 已识别的事实 */}
              {preview.extracted_facts.length > 0 && (
                <div className="mt-5">
                  <p className="mb-2 text-sm font-bold text-[#4b3e4d]">
                    已从你的描述中识别：
                  </p>
                  <ExtractedFacts
                    facts={preview.extracted_facts}
                    factEdits={factEdits}
                    editingFactId={editingFactId}
                    onEdit={(id, v) => setFactEdits((prev) => ({ ...prev, [id]: v }))}
                    onStartEdit={(id) => setEditingFactId(id)}
                    onCommitEdit={(f) => {
                      const v = factEdits[f.id] ?? f.value;
                      setFactEdits((prev) => ({ ...prev, [f.id]: v }));
                      setEditingFactId(null);
                    }}
                  />
                </div>
              )}

              {/* 关键问题 */}
              {preview.questions.length > 0 && (
                <div className="mt-5">
                  <p className="mb-2 text-sm font-bold text-[#4b3e4d]">
                    还需要你确认 {preview.questions.length} 个关键信息
                  </p>
                  <div className="space-y-4">
                    {preview.questions.map((q, idx) => (
                      <DecisionQuestion
                        key={q.id}
                        index={idx + 1}
                        question={q}
                        value={answers[q.id]}
                        onChange={(v) => setAnswer(q.id, v)}
                      />
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        {preview && (status === "ready" || status === "create_failed") && (
          <div className="flex flex-row items-center justify-between gap-3 border-t border-[#e9ded8] px-5 py-4 sm:px-7">
            <button
              onClick={handleClose}
              className="rounded-md px-3 py-2 text-sm text-[#71646e] hover:bg-[#f3ede9]"
            >
              返回修改决策
            </button>
            <button
              disabled={!canConfirm}
              onClick={handleConfirmCreate}
              className={clsx(
                "rounded-lg px-5 py-2.5 text-sm font-bold shadow-md transition",
                canConfirm
                  ? "bg-gradient-to-br from-[#30103f] to-[#4b1c62] text-white hover:-translate-y-0.5"
                  : "cursor-not-allowed bg-[#e9ded8] text-[#998e96] shadow-none",
              )}
            >
              {status === "create_failed" ? "重试创建会议" : "确认并召开会议"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** 简报卡片展示(PRD §4.2 / §8.2 / v1.1 §6.8) */
function BriefCard({
  brief,
  preview,
}: {
  brief: DecisionPathPreview["brief"];
  preview: DecisionPathPreview;
}) {
  return (
    <div className="rounded-2xl border border-[#e9ded8] bg-[#fff8f3] p-4 shadow-sm sm:p-5">
      <Section title="决策目标">
        <p className="text-sm leading-relaxed text-[#251536]">
          {brief.decision_goal}
        </p>
      </Section>
      <Section title="比较方案">
        <Pills items={brief.decision_options} />
      </Section>
      <Section title="重点分析">
        <Pills items={brief.analysis_dimensions} />
      </Section>
      {/* PRD v1.1 §6.8 / §13.2: 不再展示具体调研清单, 改为公开承诺"专家自主判断" */}
      <div className="rounded-lg border border-[#a0c8a0] bg-[#f0f8ee] px-3 py-2 text-xs text-[#3a6b3a]">
        {researchPolicyMessage(preview)}
      </div>
      <Section title="最终交付">
        <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2">
          {brief.final_deliverables.map((s) => (
            <li
              key={s}
              className="flex items-start gap-1.5 text-xs text-[#4b3e4d]"
            >
              <span className="mt-1 h-1 w-1 flex-shrink-0 rounded-full bg-[#4b1c62]" />
              <span>{s}</span>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-3 last:mb-0">
      <p className="mb-1 text-xs font-bold text-[#998e96]">{title}</p>
      {children}
    </div>
  );
}

function Pills({
  items,
  tone = "default",
}: {
  items: string[];
  tone?: "default" | "green";
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((s) => (
        <span
          key={s}
          className={clsx(
            "rounded-full border px-2.5 py-1 text-xs",
            tone === "green"
              ? "border-[#a0c8a0] bg-[#f0f8ee] text-[#3a6b3a]"
              : "border-[#ddd2cb] bg-white text-[#4b3e4d]",
          )}
        >
          {s}
        </span>
      ))}
    </div>
  );
}

function ChoiceChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        "rounded-lg border px-4 py-2 text-sm font-medium transition",
        active
          ? "border-[#4b1c62] bg-[#f0e9f2] text-[#4b1c62]"
          : "border-[#ddd2cb] bg-white text-[#4b3e4d] hover:border-[#aaa]",
      )}
    >
      {children}
    </button>
  );
}

/** 单个决策问题 — 支持所有 7 种 answer_type */
function DecisionQuestion({
  index,
  question,
  value,
  onChange,
}: {
  index: number;
  question: DecisionQuestion;
  value: string | string[] | undefined;
  onChange: (value: string | string[]) => void;
}) {
  const multi = question.answer_type === "multi_choice";
  const isText =
    question.answer_type === "short_text" ||
    question.answer_type === "number";

  function toggleMulti(opt: DecisionQuestionOption) {
    const cur = Array.isArray(value) ? value : [];
    if (cur.includes(opt.value)) {
      onChange(cur.filter((v) => v !== opt.value));
    } else {
      if (cur.length >= 3) return;
      onChange([...cur, opt.value]);
    }
  }

  if (isText) {
    return (
      <div>
        <p className="text-sm font-medium text-[#251536]">
          {index}. {question.question}
        </p>
        <p className="mb-2 text-xs text-[#998e96]">{question.why_it_matters}</p>
        <input
          type={question.answer_type === "number" ? "number" : "text"}
          className="w-full rounded-lg border border-[#e9ded8] bg-white px-3 py-2 text-sm"
          placeholder="请补充"
          value={(typeof value === "string" && value) || ""}
          onChange={(e) => onChange(e.target.value)}
        />
      </div>
    );
  }

  return (
    <div>
      <p className="text-sm font-medium text-[#251536]">
        {index}. {question.question}
        {multi && (
          <span className="ml-2 text-[10px] text-[#998e96]">(最多选 3 项)</span>
        )}
      </p>
      <p className="mb-2 text-xs text-[#998e96]">{question.why_it_matters}</p>
      <div className="flex flex-wrap gap-1.5">
        {question.options.map((opt) => {
          const active = multi
            ? Array.isArray(value) && value.includes(opt.value)
            : value === opt.value;
          return (
            <button
              key={opt.value}
              onClick={() =>
                multi ? toggleMulti(opt) : onChange(opt.value)
              }
              className={clsx(
                "rounded-lg border px-3 py-1.5 text-xs font-medium transition",
                active
                  ? "border-[#4b1c62] bg-[#f0e9f2] text-[#4b1c62]"
                  : "border-[#ddd2cb] bg-white text-[#4b3e4d] hover:border-[#aaa]",
              )}
            >
              {active ? "☑ " : ""}
              {opt.label}
            </button>
          );
        })}
      </div>
      {value === "unknown" && question.unknown_assumption && (
        <p className="mt-1.5 text-[11px] italic text-[#b58900]">
          → {question.unknown_assumption}
        </p>
      )}
    </div>
  );
}

/** 已识别事实(可点修改) — PRD §8.4 */
function ExtractedFacts({
  facts,
  factEdits,
  editingFactId,
  onEdit,
  onStartEdit,
  onCommitEdit,
}: {
  facts: ExtractedFact[];
  factEdits: Record<string, string>;
  editingFactId: string | null;
  onEdit: (id: string, value: string) => void;
  onStartEdit: (id: string) => void;
  onCommitEdit: (f: ExtractedFact) => void;
}) {
  return (
    <ul className="space-y-1.5">
      {facts.map((f) => {
        const editing = editingFactId === f.id;
        const editValue = factEdits[f.id] ?? f.value;
        const shown = editValue || f.value;
        return (
          <li
            key={f.id}
            className="flex flex-wrap items-center gap-2 text-sm text-[#4b3e4d]"
          >
            <span className="text-[#3a6b3a]">✓</span>
            <span className="font-medium">{f.label}：</span>
            {editing ? (
              <>
                <input
                  type="text"
                  className="rounded-md border border-[#e9ded8] px-2 py-1 text-xs"
                  value={editValue}
                  onChange={(e) => onEdit(f.id, e.target.value)}
                  autoFocus
                />
                <button
                  onClick={() => onCommitEdit(f)}
                  className="rounded-md bg-[#4b1c62] px-2 py-1 text-[10px] text-white"
                >
                  保存
                </button>
              </>
            ) : (
              <>
                <span>{shown}</span>
                <button
                  onClick={() => onStartEdit(f.id)}
                  className="text-[10px] text-[#71646e] underline-offset-2 hover:underline"
                >
                  修改
                </button>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}
