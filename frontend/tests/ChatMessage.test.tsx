import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";
import { ChatMessage } from "@/components/ChatMessage";
import type { TranscriptEntry } from "@/lib/types";

/** Batch 1 后端真实产出的四段契约格式（parallel_research._render_public_research_statement） */
const V3_CONTENT = [
  "【当前判断 · 谨慎推进】当前更适合继续了解机会，而不是立即承诺。",
  "【成本与回报】\n· 留任收益含稳定现金流；\n· 跳槽收益依赖尚未确认的股权条款。",
  "【建议行动】先取得书面股权条件再比较。",
  "【关键问题】如果股权最终价值为零，你还愿意接受吗？",
].join("\n\n");

const v3Entry: TranscriptEntry = {
  speaker: "economist",
  round: 0,
  type: "research",
  stance: "cautious",
  content: V3_CONTENT,
  evidence: [],
  confidence: 0.68,
  lens_title: "成本与回报",
  silent: false,
  // 历史字段仍在旧数据里，新渲染必须忽略
  basis_level: "limited",
  duty: "交叉质证",
};

const legacyEntry: TranscriptEntry = {
  speaker: "artist",
  round: 1,
  type: "debate",
  stance: "for",
  content: "【我的判断】这是一个值得重构的问题。",
  basis_level: "moderate",
  duty: "提出第三选项",
};

const legacyStanceEntry: TranscriptEntry = {
  speaker: "politician",
  round: 2,
  type: "debate",
  stance: "against",
  content: "【我的判断】目前筹码不足。",
};

describe("ChatMessage · V3 四段契约渲染", () => {
  it("渲染四段契约标题与新 stance 徽章", () => {
    render(<ChatMessage entry={v3Entry} />);
    expect(screen.getByText("谨慎推进")).toBeInTheDocument();
    expect(screen.getByText(/当前更适合继续了解机会/)).toBeInTheDocument();
    expect(screen.getByText("【成本与回报】")).toBeInTheDocument();
    expect(screen.getByText("【建议行动】")).toBeInTheDocument();
    expect(screen.getByText("【关键问题】")).toBeInTheDocument();
  });

  it("「独立判断」只出现一次；不渲染 duty chip 和判断基础标签", () => {
    render(<ChatMessage entry={v3Entry} />);
    expect(screen.getAllByText("独立判断")).toHaveLength(1);
    expect(screen.queryByText("交叉质证")).toBeNull();
    expect(screen.queryByText(/判断基础/)).toBeNull();
  });

  it("历史议会旧枚举 for/against 与已删除专家（artist）正常渲染", () => {
    render(<ChatMessage entry={legacyEntry} />);
    expect(screen.getByText("艺术家")).toBeInTheDocument();
    expect(screen.getByText("支持")).toBeInTheDocument();
    expect(screen.getByText("第 1 轮")).toBeInTheDocument();
  });

  it("历史 against 徽章 + politician 渲染不崩", () => {
    render(<ChatMessage entry={legacyStanceEntry} />);
    expect(screen.getByText("政治家")).toBeInTheDocument();
    expect(screen.getByText("反对")).toBeInTheDocument();
  });

  it("debate_degraded 以斜体淡化展示（不红色）", () => {
    render(
      <ChatMessage
        entry={{
          speaker: "historian",
          round: 1,
          type: "debate_degraded",
          content: "本轮只形成了有限观察。",
        }}
      />
    );
    const bubble = screen.getByText(/本轮只形成了有限观察/).closest("div");
    expect(bubble?.className).toContain("italic");
    expect(bubble?.className).toContain("text-ink-muted");
  });
});
