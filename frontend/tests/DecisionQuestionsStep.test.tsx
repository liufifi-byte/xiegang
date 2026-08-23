import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
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
  it("第一题不显示上一题，后续题显示", () => {
    // 父组件在 index=0 时不传 onBack
    const props: React.ComponentProps<typeof DecisionQuestionsStep> = {
      questions, index: 0, answers: {}, error: null, onChange: vi.fn(),
      onNext: vi.fn(), onUnknown: vi.fn(), onCreate: vi.fn(),
      onClose: vi.fn(), creating: false,
    };
    const first = render(<DecisionQuestionsStep {...props} />);
    expect(first.container.textContent).not.toContain("上一题");
    first.unmount();
    const second = renderStep({ index: 1 });
    expect(screen.getByRole("button", { name: "上一题" })).toBeInTheDocument();
  });

  it("单步流程：不出现确认任务步骤条", () => {
    renderStep();
    expect(screen.queryByText("确认任务")).toBeNull();
    expect(screen.getByText("问题 1 / 2")).toBeInTheDocument();
  });


  it("问题与为什么重要合并在浅底卡片内，选项为胶囊", () => {
    renderStep();
    const why = screen.getByText("时间窗口会改变建议权重。");
    const card = why.closest("div.rounded-xl");
    expect(card).not.toBeNull();
    expect(card).toContainElement(screen.getByText("什么时候做决定？"));
    expect(screen.getByText("三个月内").closest("button")).toHaveClass("rounded-full");
    expect(screen.getByText("问题 1 / 2")).toBeInTheDocument();
  });

  it("多选题显示最多 3 项提示", () => {
    renderStep({ questions: [{
      id: "m", question: "最看重哪些方面？", why_it_matters: "影响权重。",
      answer_type: "multi_choice",
      options: [{ value: "a", label: "收入" }, { value: "b", label: "稳定" }],
    }], index: 0 });
    expect(screen.getByText("可选择最多 3 项")).toBeInTheDocument();
  });
});
