import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { DecisionQuestionsStep } from "@/components/council/DecisionQuestionsStep";
import type { DecisionQuestion } from "@/lib/types";

const questions: DecisionQuestion[] = [
  { id: "horizon", question: "什么时候做决定？", why_it_matters: "时间窗口会改变建议权重。", answer_type: "single_choice", options: [{ value: "soon", label: "三个月内" }, { value: "later", label: "六至十八个月" }] },
  { id: "budget", question: "每月可承受多少支出？", why_it_matters: "用于计算安全边界。", answer_type: "number", options: [] },
];

function renderStep(overrides: Partial<React.ComponentProps<typeof DecisionQuestionsStep>> = {}) {
  const props: React.ComponentProps<typeof DecisionQuestionsStep> = {
    questions, index: 0, answers: {}, error: null, onChange: vi.fn(), onBack: vi.fn(), onNext: vi.fn(), onUnknown: vi.fn(), onCreate: vi.fn(), onClose: vi.fn(), creating: false, ...overrides,
  };
  return { ...render(<DecisionQuestionsStep {...props} />), props };
}

describe("DecisionQuestionsStep", () => {
  it("shows progress and reports an unknown answer", () => {
    const { props } = renderStep();
    expect(screen.getByText("问题 1 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "暂时不知道，交给专家判断" }));
    expect(props.onUnknown).toHaveBeenCalledTimes(1);
  });

  it("keeps next disabled until the current question has an answer", () => {
    const view = renderStep();
    expect(screen.getByRole("button", { name: "继续 →" })).toBeDisabled();
    view.rerender(<DecisionQuestionsStep {...view.props} answers={{ horizon: "soon" }} />);
    expect(screen.getByRole("button", { name: "继续 →" })).not.toBeDisabled();
  });

  it("renders the final confirmation action on the last question", () => {
    renderStep({ index: 1, answers: { budget: "12000" } });
    expect(screen.getByRole("button", { name: "确认并召开会议" })).toBeEnabled();
  });
});
