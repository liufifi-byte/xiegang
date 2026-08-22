/**
 * 快速确认页测试（规范 2026-08-22-quick-confirmation-modal-design §11）。
 */
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { TaskConfirmationStep } from "@/components/council/TaskConfirmationStep";
import type { DecisionPathPreview } from "@/lib/types";

const preview: DecisionPathPreview = {
  version: "v2",
  generation_id: "gen-1",
  category: "career",
  brief: {
    decision_goal: "在三个月内决定是否从字节跳槽到国企 offer。",
    decision_options: ["留在字节", "接受国企 offer", "谈延长考虑期", "第三方内推再比较"],
    analysis_dimensions: ["薪酬总包对比", "竞业限制风险", "职业发展空间", "工作强度与健康"],
    final_deliverables: ["两方案利弊对比表", "风险清单与退出条件", "下一步行动建议"],
  },
  extracted_facts: [],
  questions: [],
  assumptions: [],
  fallback_used: false,
};

function renderStep(overrides: Partial<React.ComponentProps<typeof TaskConfirmationStep>> = {}) {
  const props: React.ComponentProps<typeof TaskConfirmationStep> = {
    preview,
    editingBrief: false,
    briefFeedback: "",
    error: null,
    hasPendingBriefFeedback: false,
    busy: false,
    onEditingChange: vi.fn(),
    onFeedbackChange: vi.fn(),
    onRegenerate: vi.fn(),
    onContinue: vi.fn(),
    onBack: vi.fn(),
    maxFeedback: 500,
    ...overrides,
  };
  return { ...render(<TaskConfirmationStep {...props} />), props };
}

describe("TaskConfirmationStep 快速确认页", () => {
  it("1. 渲染目标、全部比较方案、全部分析维度和全部最终交付", () => {
    renderStep();
    expect(screen.getByText(preview.brief.decision_goal)).toBeInTheDocument();
    // 全量：4 个方案（旧版 slice(0,3) 只显示 3）
    for (const option of preview.brief.decision_options) {
      expect(screen.getByText(option)).toBeInTheDocument();
    }
    // 全量：4 个维度（旧版 join 成一段）
    for (const dim of preview.brief.analysis_dimensions) {
      expect(screen.getByText(dim)).toBeInTheDocument();
    }
    // 全量：3 个交付（旧版 slice(0,2)）
    for (const item of preview.brief.final_deliverables) {
      expect(screen.getByText(item)).toBeInTheDocument();
    }
  });

  it("2. fallback 提示位于目标卡片内部，旧黄色横幅不存在", () => {
    renderStep({ preview: { ...preview, fallback_used: true } });
    expect(
      screen.getByText("当前内容根据已有信息生成，你可以在确认前修改。")
    ).toBeInTheDocument();
    expect(
      screen.queryByText("已根据场景生成基础简报，你仍可以调整理解。")
    ).toBeNull();
  });

  it("3. 默认不显示 textarea；点击修改入口后展开", () => {
    const view = renderStep();
    expect(screen.queryByLabelText("需要修正什么？")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "信息不准确？修改任务理解" }));
    expect(view.props.onEditingChange).toHaveBeenCalledWith(true);

    view.rerender(
      <TaskConfirmationStep {...view.props} editingBrief />
    );
    expect(screen.getByLabelText("需要修正什么？")).toBeInTheDocument();
    // 展开后按钮文案变化 + aria-expanded
    expect(screen.getByRole("button", { name: "收起修改" })).toHaveAttribute(
      "aria-expanded", "true"
    );
  });

  it("4. 空修正内容时重新梳理禁用；输入内容后启用", () => {
    const view = renderStep({ editingBrief: true });
    const regenerate = screen.getByRole("button", { name: "重新梳理任务" });
    expect(regenerate).toBeDisabled();
    view.rerender(
      <TaskConfirmationStep {...view.props} briefFeedback="我更关注长期成长" />
    );
    expect(screen.getByRole("button", { name: "重新梳理任务" })).toBeEnabled();
  });

  it("5. 默认状态点主按钮直接 onContinue，无需中间确认", () => {
    const view = renderStep();
    const primary = screen.getByRole("button", { name: "确认，继续补充信息 →" });
    expect(primary).toBeEnabled();
    fireEvent.click(primary);
    expect(view.props.onContinue).toHaveBeenCalledTimes(1);
  });

  it("6. 存在未应用修改时不能直接继续（busy 同理）", () => {
    const view = renderStep({ hasPendingBriefFeedback: true });
    expect(screen.getByRole("button", { name: "确认，继续补充信息 →" })).toBeDisabled();

    view.rerender(
      <TaskConfirmationStep {...view.props} hasPendingBriefFeedback={false} busy />
    );
    expect(screen.getByRole("button", { name: "确认，继续补充信息 →" })).toBeDisabled();
    // 禁用文案保持可读（非过低对比度由样式保证，此处断言按钮仍渲染文本）
    expect(screen.getByRole("button", { name: "确认，继续补充信息 →" }).textContent)
      .toContain("确认，继续补充信息");
  });

  it("7. 旧文案不再出现", () => {
    renderStep();
    expect(screen.queryByText("这个任务理解准确吗？")).toBeNull();
    expect(screen.queryByText("按这个目标继续")).toBeNull();
    expect(screen.queryByText("✓ 理解正确，按这个目标继续")).toBeNull();
    expect(screen.queryByText("理解不准确？")).toBeNull();
    expect(screen.queryByRole("button", { name: "理解正确" })).toBeNull();
  });

  it("8. 保留错误信息、返回操作和字数上限", () => {
    renderStep({
      editingBrief: true,
      briefFeedback: "abc",
      error: "网络异常，请重试",
    });
    expect(screen.getByText("网络异常，请重试")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回修改决策" })).toBeEnabled();
    expect(screen.getByText("3/500")).toBeInTheDocument();
    expect(screen.getByLabelText("需要修正什么？")).toHaveAttribute("maxlength", "500");
  });

  it("修改入口点击收起时通知父级关闭", () => {
    const view = renderStep({ editingBrief: true, briefFeedback: "关注长期成长" });
    fireEvent.click(screen.getByRole("button", { name: "收起修改" }));
    expect(view.props.onEditingChange).toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "重新梳理任务" }));
    expect(view.props.onRegenerate).toHaveBeenCalledTimes(1);
  });
});
