"use client";

import { useMemo, useState } from "react";
import { ClarificationQuestion } from "@/lib/types";

interface Props {
  questions: ClarificationQuestion[];
  onSubmit: (answers: Array<{ question_id: string; value: string }>) => Promise<void>;
  onSkip: () => Promise<void>;
}

export function ClarificationModal({ questions, onSubmit, onSkip }: Props) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const complete = useMemo(
    () => questions.every((question) => (answers[question.id] || "").trim()),
    [answers, questions]
  );

  const submit = async () => {
    if (!complete || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(
        questions.map((question) => ({
          question_id: question.id,
          value: answers[question.id].trim(),
        }))
      );
    } catch {
      setError("提交没有成功，请检查网络后重试。你填写的内容仍保留在这里。");
    } finally {
      setSubmitting(false);
    }
  };

  const skip = async () => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSkip();
    } catch {
      setError("暂时无法继续，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center overflow-y-auto bg-[#352b34]/25 p-4 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="clarification-title"
    >
      <section className="my-auto w-full max-w-2xl rounded-[22px] border border-[#dfd2c9] bg-[#fffdf9] p-5 shadow-[0_24px_80px_rgba(56,35,48,0.2)] sm:p-7">
        <p className="text-[10px] font-extrabold uppercase tracking-[0.14em] text-[#8e4769]">
          Before the council begins
        </p>
        <h2
          id="clarification-title"
          className="mt-2 text-xl font-extrabold text-[#30252e] sm:text-2xl"
        >
          再补充一点，专家才能判断得更准
        </h2>
        <p className="mt-2 text-sm leading-6 text-[#786d75]">
          以下信息可能直接改变会议结论。回答通常不到一分钟；如果现在不确定，也可以继续分析。
        </p>

        <div className="mt-5 divide-y divide-[#eee4dc]">
          {questions.map((question, index) => (
            <fieldset key={question.id} className="py-5 first:pt-1">
              <legend className="flex items-start text-sm font-bold leading-6 text-[#352b33] sm:text-[15px]">
                <span className="mr-2 mt-0.5 grid h-6 w-6 flex-none place-items-center rounded-lg bg-[#f2e5ec] text-[11px] font-extrabold text-[#7b375d]">
                  {index + 1}
                </span>
                {question.question}
              </legend>
              <p className="ml-8 mt-1.5 text-xs leading-5 text-[#8a7c84]">
                为什么要问：{question.why_it_matters}
              </p>

              {question.answer_type === "single_choice" ? (
                <div className="ml-8 mt-3 flex flex-wrap gap-2">
                  {question.options.map((option) => {
                    const selected = answers[question.id] === option;
                    return (
                      <button
                        key={option}
                        type="button"
                        onClick={() =>
                          setAnswers((current) => ({
                            ...current,
                            [question.id]: option,
                          }))
                        }
                        className={`rounded-xl border px-3 py-2 text-xs transition ${
                          selected
                            ? "border-[#6f3458] bg-[#f7eaf1] font-bold text-[#6f3458]"
                            : "border-[#dccfc8] bg-white text-[#645a61] hover:border-[#9b7087]"
                        }`}
                      >
                        {option}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <input
                  value={answers[question.id] || ""}
                  onChange={(event) =>
                    setAnswers((current) => ({
                      ...current,
                      [question.id]: event.target.value,
                    }))
                  }
                  inputMode={question.answer_type === "number" ? "decimal" : "text"}
                  placeholder={
                    question.answer_type === "number"
                      ? "请输入数字"
                      : "请简短回答"
                  }
                  className="ml-8 mt-3 w-[calc(100%-2rem)] rounded-xl border border-[#dccfc8] bg-white px-3.5 py-2.5 text-sm text-[#352b33] outline-none transition focus:border-[#6f3458] focus:ring-2 focus:ring-[#6f3458]/10"
                />
              )}
            </fieldset>
          ))}
        </div>

        {error && (
          <p className="rounded-xl bg-[#fff0ed] px-3 py-2 text-xs leading-5 text-[#a44343]">
            {error}
          </p>
        )}

        <div className="mt-5 flex flex-col-reverse gap-3 border-t border-[#eee4dc] pt-5 sm:flex-row sm:items-center sm:justify-between">
          <button
            type="button"
            onClick={skip}
            disabled={submitting}
            className="px-2 py-2 text-sm font-medium text-[#776b73] disabled:opacity-50"
          >
            暂时不知道，继续分析
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!complete || submitting}
            className="rounded-xl bg-[#4c2442] px-5 py-3 text-sm font-bold text-white transition hover:bg-[#613155] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "正在提交…" : "提交并开始会议 →"}
          </button>
        </div>
      </section>
    </div>
  );
}
