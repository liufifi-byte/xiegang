/** 调研阶段席位文案（用户反馈：独立调研→调研阶段；完成后提示辩论） */
import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { ExpertSeatPanel } from "@/components/council/ExpertSeatPanel";

const IDS = ["economist", "psychologist"] as const;

describe("ExpertSeatPanel 调研阶段文案", () => {
  it("默认（非调研阶段）显示微观状态标签", () => {
    render(<ExpertSeatPanel expertIds={[...IDS]} statuses={{ economist: "thinking" }} activeMember={null} onSelect={vi.fn()} />);
    expect(screen.getByText("正在判断")).toBeInTheDocument();
    expect(screen.getByText("等待中")).toBeInTheDocument();
  });

  it("调研阶段：所有席位统一显示「调研阶段」", () => {
    render(<ExpertSeatPanel researchPhase expertIds={[...IDS]} statuses={{ economist: "using_tool" }} activeMember={null} onSelect={vi.fn()} />);
    expect(screen.getAllByText("调研阶段")).toHaveLength(2);
    expect(screen.queryByText("正在查证")).toBeNull();
  });

  it("调研全员完成：席位显示「已调研完成」", () => {
    render(<ExpertSeatPanel researchPhase researchAllDone expertIds={[...IDS]} statuses={{}} activeMember={null} onSelect={vi.fn()} />);
    expect(screen.getAllByText("已调研完成")).toHaveLength(2);
  });
});
