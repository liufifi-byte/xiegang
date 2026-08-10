"use client";

import clsx from "clsx";
import React, { useEffect, useRef } from "react";
import type { DecisionQuestion, DecisionQuestionOption } from "@/lib/types";

const UNKNOWN_VALUE = "unknown";

interface Props {
  questions: DecisionQuestion[];
  index: number;
  answers: Record<string, string | string[]>;
  error: string | null;
  onChange: (id: string, value: string | string[]) => void;
  onBack: () => void;
  onNext: () => void;
  onUnknown: () => void;
  onCreate: () => void;
  onClose: () => void;
  creating: boolean;
}

export function DecisionQuestionsStep({ questions, index, answers, error, onChange, onBack, onNext, onUnknown, onCreate, onClose, creating }: Props) {
  const question = questions[index];
  const headingRef = useRef<HTMLHeadingElement>(null);
  const value = question ? answers[question.id] : undefined;
  const answered = value !== undefined && value !== "" && (!Array.isArray(value) || value.length > 0);
  const last = index === questions.length - 1;

  useEffect(() => { headingRef.current?.focus(); }, [index]);

  if (!question) {
    return (
      <div className="py-10 text-center">
        <p className="text-sm text-[#50627a]">没有需要补充的问题。</p>
        <button type="button" onClick={onCreate} className="mt-5 rounded-lg bg-[#172d4d] px-5 py-3 text-sm font-bold text-white">确认并召开会议</button>
      </div>
    );
  }

  function choose(value: string | string[]) {
    onChange(question.id, value);
    if (!multiChoice(question) && value !== UNKNOWN_VALUE) {
      window.setTimeout(onNext, 200);
    }
  }

  return (
    <>
      <div className="mb-6 flex items-center gap-2 text-[11px] font-bold text-[#172d4d]">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#172d4d] text-white">✓</span>
        <span className="text-[#6f8095]">确认任务</span>
        <span className="h-px flex-1 bg-[#172d4d]" />
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#172d4d] text-white">2</span>
        <span>补充信息</span>
        <span className="ml-auto text-[#718096]">问题 {index + 1} / {questions.length}</span>
      </div>

      <div className="mb-6 h-1 overflow-hidden rounded-full bg-[#e8edf3]" aria-label={`问题进度 ${index + 1}/${questions.length}`}>
        <div className="h-full rounded-full bg-[#386998] transition-all" style={{ width: `${((index + 1) / questions.length) * 100}%` }} />
      </div>

      <h3 ref={headingRef} tabIndex={-1} className="text-xl font-bold leading-8 text-[#172d4d] outline-none">{question.question}</h3>
      <p className="mt-2 text-sm leading-6 text-[#718096]">{question.why_it_matters}</p>

      <AnswerControl question={question} value={value} onChange={(next) => choose(next)} />

      {value === UNKNOWN_VALUE && question.unknown_assumption && (
        <p className="mt-3 rounded-lg bg-[#f5f7fa] px-3 py-2 text-xs leading-5 text-[#60728a]">专家会把这项当作待验证假设：{question.unknown_assumption}</p>
      )}
      {error && <p className="mt-4 rounded-lg bg-[#fff0ef] px-3 py-2 text-xs leading-5 text-[#a43f48]">{error}</p>}

      <div className="mt-8 flex flex-col-reverse gap-3 border-t border-[#e6eaf0] pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <button type="button" onClick={onBack} className="rounded-lg px-2 py-2 text-sm text-[#657892] hover:bg-[#f3f6f9]">上一步</button>
          <button type="button" onClick={onClose} disabled={creating} className="text-xs text-[#8a98a9] hover:text-[#50627a]">退出</button>
        </div>
        <div className="flex items-center gap-2">
          {!answered && <button type="button" onClick={onUnknown} className="rounded-lg px-3 py-2 text-xs font-medium text-[#657892] hover:bg-[#f3f6f9]">暂时不知道，交给专家判断</button>}
          <button type="button" disabled={!answered || creating} onClick={last ? onCreate : onNext} className="rounded-lg bg-[#172d4d] px-5 py-3 text-sm font-bold text-white shadow-sm transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:bg-[#d8dee7] disabled:text-[#8592a3] disabled:shadow-none">
            {creating ? "正在创建…" : last ? "确认并召开会议" : "继续 →"}
          </button>
        </div>
      </div>
    </>
  );
}

function AnswerControl({ question, value, onChange }: { question: DecisionQuestion; value: string | string[] | undefined; onChange: (value: string | string[]) => void }) {
  const text = question.answer_type === "short_text" || question.answer_type === "number";
  if (text) {
    return <input autoFocus={false} type={question.answer_type === "number" ? "number" : "text"} inputMode={question.answer_type === "number" ? "decimal" : "text"} value={typeof value === "string" && value !== UNKNOWN_VALUE ? value : ""} onChange={(event) => onChange(event.target.value)} placeholder={question.custom_placeholder || (question.answer_type === "number" ? "请输入数字" : "请简短回答")} className="mt-7 w-full rounded-xl border border-[#d7e0ea] bg-white px-4 py-3 text-sm text-[#263b57] outline-none focus:border-[#386998] focus:ring-2 focus:ring-[#386998]/15" />;
  }

  const multi = question.answer_type === "multi_choice";
  const selected = Array.isArray(value) ? value : [];
  return <div className="mt-7 space-y-2">{question.options.filter((option) => option.value !== UNKNOWN_VALUE).map((option) => {
    const active = multi ? selected.includes(option.value) : value === option.value;
    return <button key={option.value} type="button" onClick={() => multi ? toggleMulti(selected, option, onChange) : onChange(option.value)} className={clsx("flex min-h-12 w-full items-center justify-between rounded-xl border px-4 py-3 text-left text-sm transition", active ? "border-[#386998] bg-[#edf3f9] font-bold text-[#172d4d]" : "border-[#d7e0ea] bg-white text-[#41546d] hover:border-[#7d9ab8]") }><span>{option.label}</span><span className="ml-3 text-xs text-[#8391a2]">{active ? "✓" : ""}</span></button>;
  })}</div>;
}

function multiChoice(question: DecisionQuestion) {
  return question.answer_type === "multi_choice";
}

function toggleMulti(current: string[], option: DecisionQuestionOption, onChange: (value: string | string[]) => void) {
  if (current.includes(option.value)) onChange(current.filter((value) => value !== option.value));
  else if (current.length < 3) onChange([...current, option.value]);
}
