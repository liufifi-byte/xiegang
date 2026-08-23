/** 会前流程改版：无确认卡；零问题自动开会；有问题只显示追问。 */
import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  previewDecisionPath: vi.fn(),
  createParliamentV2: vi.fn(),
  deleteDecisionPath: vi.fn(),
}));

import * as api from "@/lib/api";
import { DecisionPathModal } from "@/components/council/DecisionPathModal";

const BASE = {
  open: true, topic: "要不要跳槽", topicCategory: null,
  userBackground: null, selectedExpertIds: null,
  onClose: vi.fn(), onParliamentCreated: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("DecisionPathModal 无确认卡流程", () => {
  it("零问题：不弹确认/追问，直接创建会议", async () => {
    (api.previewDecisionPath as any).mockResolvedValue({
      generation_id: "g1", questions: [], brief: {} as any,
    } as any);
    (api.createParliamentV2 as any).mockResolvedValue({ parliament_id: "p1" });
    render(<DecisionPathModal {...BASE} />);
    await waitFor(() => expect(api.createParliamentV2).toHaveBeenCalledTimes(1));
    expect(BASE.onParliamentCreated).toHaveBeenCalledWith("p1");
    expect(screen.queryByText("确认本次决策任务")).toBeNull();
  });

  it("有问题：直接显示追问，无确认任务步骤", async () => {
    (api.previewDecisionPath as any).mockResolvedValue({
      generation_id: "g2",
      questions: [{ id: "q1", question: "什么时候做决定？", why_it_matters: "x",
        answer_type: "single_choice",
        options: [{ value: "a", label: "三个月内" }] }],
      brief: {} as any,
    } as any);
    render(<DecisionPathModal {...BASE} />);
    await waitFor(() => screen.getByText("什么时候做决定？"));
    expect(screen.queryByText("确认本次决策任务")).toBeNull();
    expect(screen.queryByText("信息不准确？修改任务理解")).toBeNull();
    expect(api.createParliamentV2).not.toHaveBeenCalled();
  });
});
