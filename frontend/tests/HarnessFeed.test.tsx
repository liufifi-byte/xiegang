/**
 * Phase 7：§15.1 产品事件 → 用户语言卡片的映射与渲染。
 * spec §15.2：不暴露隐藏推理/系统 Prompt/工具参数/原始搜索/堆栈。
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  HARNESS_EVENT_TYPES,
  describeHarnessEvent,
} from "@/lib/harnessEvents";
import { HarnessFeed } from "@/components/council/HarnessFeed";

const ev = (type: string, payload: Record<string, any> = {}) => ({
  type,
  event_type: type,
  payload,
});

describe("describeHarnessEvent", () => {
  it("计划事件给用户语言与专家数", () => {
    const card = describeHarnessEvent(ev("plan_created", {
      objective: "两周内完成跳槽决策评估",
      expert_tasks: ["economist: 现金流", "psychologist: 动机"],
    }));
    expect(card?.kind).toBe("plan");
    expect(card?.title).toContain("研究计划");
    expect(card?.detail).toContain("跳槽决策");
    expect(card?.tags).toEqual(["拟请 2 位专家"]);
  });

  it("专家失败是降级文案，不是技术错误", () => {
    const card = describeHarnessEvent(ev("agent_failed", {
      agent_id: "economist", message: "ConnectionResetError traceback...",
    }));
    expect(card?.title).toContain("经济学家");
    expect(card?.title).toContain("没能完成");
    // §15.2：堆栈与技术细节不外泄
    expect(JSON.stringify(card)).not.toContain("traceback");
    expect(card?.detail).toContain("会议继续");
  });

  it("争议卡片显示参与专家与立场变化", () => {
    const opened = describeHarnessEvent(ev("dispute_opened", {
      question: "现金流是否足够", participants: ["economist", "strategist"],
    }));
    expect(opened?.title).toContain("实质分歧");
    expect(opened?.tags?.[0]).toBe("经济学家 vs 策略家");

    const msg = describeHarnessEvent(ev("debate_message", {
      agent_id: "economist", changed: true, position_after: "cautious",
    }));
    expect(msg?.tags).toEqual(["调整了立场", "谨慎"]);
  });

  it("来源接受/拒绝使用可理解理由", () => {
    expect(describeHarnessEvent(ev("evidence_rejected", { reason: "stale" }))
      ?.detail).toBe("信息已过时");
    expect(describeHarnessEvent(ev("evidence_rejected", { reason: "low_reliability" }))
      ?.detail).toBe("来源可靠性不足");
    const ok = describeHarnessEvent(ev("evidence_added", {
      source_domain: "www.stats.gov.cn",
    }));
    expect(ok?.detail).toContain("www.stats.gov.cn");
    expect(ok?.title).not.toContain("excerpt"); // 摘要原文不外泄
  });

  it("等待用户与终局需要用户注意", () => {
    const waiting = describeHarnessEvent(ev("user_input_requested", {
      questions: ["期权条款能拿到书面版吗？"],
    }));
    expect(waiting?.attention).toBe(true);
    expect(waiting?.detail).toContain("期权条款");

    const final = describeHarnessEvent(ev("decision_finalized", {
      strength: "conditional",
    }));
    expect(final?.attention).toBe(true);
    expect(final?.detail).toContain("适用条件");

    const partial = describeHarnessEvent(ev("partial_finalized", {}));
    expect(partial?.title).toContain("阶段性");
  });

  it("终局卡优先展示主席结论四段", () => {
    const card = describeHarnessEvent(ev("decision_finalized", {
      strength: "conditional",
      chair_summary: "当前建议：暂缓签约，先完成现金流核算。\n已确认：月供=2.1万\n推进条件：税后收入确认\n下一步补充：公积金额度",
    }));
    expect(card?.attention).toBe(true);
    expect(card?.detail).toContain("当前建议：暂缓签约");
    expect(card?.detail).toContain("下一步补充：公积金额度");
    expect(card?.tags).toContain("结论带适用条件，请注意查看");
  });

  it("降级与恢复语义可理解", () => {
    expect(describeHarnessEvent(ev("run_log", { note: "research_degraded" }))
      ?.title).toContain("网络检索暂时不可用");
    expect(describeHarnessEvent(ev("run_log", { note: "runtime_recovered_x" }))
      ).toBe(null);
    expect(describeHarnessEvent(ev("runtime_recovered", {}))?.title)
      .toContain("恢复");
    // 阶段切换噪声不展示
    expect(describeHarnessEvent(ev("run_log", { note: "phase", phase: "DEBATE_LOOP" })))
      .toBe(null);
  });

  it("legacy 与未知事件返回 null（交回旧管线）", () => {
    for (const legacy of ["parliament_started", "agent_trace", "summary",
                          "node_complete", "nonexistent_type"]) {
      expect(describeHarnessEvent(ev(legacy))).toBeNull();
    }
  });

  it("§15.1 白名单完整", () => {
    for (const t of ["plan_created", "user_input_requested", "agent_failed",
                     "dispute_opened", "evidence_added", "audit_passed",
                     "decision_finalized", "partial_finalized",
                     "runtime_recovered", "run_log"]) {
      expect(HARNESS_EVENT_TYPES.has(t)).toBe(true);
    }
    expect(HARNESS_EVENT_TYPES.has("parliament_started")).toBe(false);
  });
});

describe("HarnessFeed", () => {
  it("只渲染有翻译的事件，隐藏静默项", () => {
    render(
      <HarnessFeed
        items={[
          { type: "plan_created", payload: { objective: "评估跳槽决策" } },
          { type: "agent_partial", payload: {} }, // 静默
          { type: "agent_failed", payload: { agent_id: "historian" } },
        ]}
      />
    );
    expect(screen.getByText(/研究计划/)).toBeTruthy();
    expect(screen.getByText(/历史学家/)).toBeTruthy();
    expect(screen.queryByText(/agent_partial/)).toBeNull();
  });

  it("空事件列表不渲染面板", () => {
    const { container } = render(<HarnessFeed items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
