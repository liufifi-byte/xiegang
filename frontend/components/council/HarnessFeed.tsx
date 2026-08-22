"use client";

/**
 * Decision Harness（V4）事件流面板：把 §15.1 事件翻译成用户语言。
 * 只渲染 describeHarnessEvent 产出的卡片——原始事件绝不直出（spec §15.2）。
 */
import { describeHarnessEvent, type HarnessCard } from "@/lib/harnessEvents";

const KIND_STYLES: Record<string, { border: string; label: string }> = {
  plan: { border: "border-violet-300", label: "计划" },
  expert: { border: "border-sky-300", label: "专家" },
  dispute: { border: "border-amber-300", label: "分歧" },
  evidence: { border: "border-emerald-300", label: "查证" },
  audit: { border: "border-teal-300", label: "核查" },
  status: { border: "border-stone-300", label: "状态" },
  final: { border: "border-rose-300", label: "结论" },
  user: { border: "border-indigo-300", label: "等你" },
};

export interface HarnessFeedItem {
  /** 原始事件（只取 type 用于 key） */
  type: string;
  seq?: number;
  payload?: Record<string, unknown>;
}

export function HarnessFeed({ items }: { items: HarnessFeedItem[] }) {
  const cards: Array<HarnessCard & { key: string }> = [];
  for (const item of items) {
    const card = describeHarnessEvent(item);
    if (card) {
      cards.push({ ...card, key: `${item.type}-${cards.length}` });
    }
  }
  if (!cards.length) return null;
  const visible = cards.slice(-12); // 面板只保留最近 12 张卡片

  return (
    <section
      aria-label="会议进行中"
      className="rounded-2xl border border-stone-200 bg-white/80 p-4"
    >
      <h2 className="mb-3 text-sm font-medium text-stone-500">
        正在进行的分析
      </h2>
      <ul className="space-y-2">
        {visible.map((card) => {
          const style = KIND_STYLES[card.kind] ?? KIND_STYLES.status;
          return (
            <li
              key={card.key}
              className={`rounded-xl border-l-4 ${style.border} bg-stone-50 px-3 py-2 ${
                card.attention ? "ring-1 ring-indigo-200" : ""
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="rounded bg-stone-200/70 px-1.5 py-0.5 text-[10px] text-stone-600">
                  {style.label}
                </span>
                <span className="text-sm font-medium text-stone-800">
                  {card.title}
                </span>
                {card.tags?.filter(Boolean).map((tag) => (
                  <span
                    key={tag}
                    className="rounded-full bg-white px-2 py-0.5 text-[10px] text-stone-500 border border-stone-200"
                  >
                    {tag}
                  </span>
                ))}
              </div>
              {card.detail && (
                <p className="mt-1 whitespace-pre-line text-xs leading-5 text-stone-600">
                  {card.detail}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
