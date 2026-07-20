"use client";

import { useState } from "react";
import { TOPIC_TEMPLATES } from "@/lib/types";

interface Props {
  onSubmit: (
    topic: string,
    category: string | null,
    background: string
  ) => void;
  loading?: boolean;
}

export function HomeSceneTabs({ onSubmit, loading }: Props) {
  const [topic, setTopic] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [background, setBackground] = useState("");

  const handleSceneClick = (cat: string, displayName: string) => {
    setCategory(cat);
    if (!topic.trim()) setTopic(`要不要${displayName}？`);
  };

  const handleSubmit = () => {
    if (!topic.trim() || loading) return;
    onSubmit(topic, category, background);
  };

  return (
    <div className="max-w-3xl mx-auto px-6 pb-32">
      {/* 大标题区 */}
      <header className="pt-24 pb-16 text-center">
        <h1 className="font-serif text-6xl font-medium tracking-tight text-ink mb-4">
          邪杠会议室
        </h1>
        <p className="leading-relaxed">
          <span className="text-base text-ink-2 tracking-wide">先</span>
          <span className="text-gold font-serif text-5xl font-bold mx-1.5 leading-none">杠</span>
          <span className="text-base text-ink-2 tracking-wide">一杠，再下结论</span>
        </p>
      </header>

      {/* 8 场景网格 */}
      <section className="mb-12">
        <div className="flex items-center gap-3 mb-5">
          <span className="text-xs uppercase tracking-wider text-ink-2 font-medium">
            精选场景
          </span>
          <span className="flex-1 h-px bg-edge" />
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {TOPIC_TEMPLATES.map((t) => (
            <button
              key={t.category}
              onClick={() => handleSceneClick(t.category, t.display_name)}
              className={`group p-5 rounded-md border text-left transition-all ${
                category === t.category
                  ? "border-brand bg-white ring-1 ring-brand/30"
                  : "bg-bg-surface border-transparent hover:border-edge-strong hover:bg-white"
              }`}
            >
              <div className="text-base font-medium text-ink leading-none mb-1.5">
                {t.display_name}
              </div>
              <div className="text-xs text-ink-3 tracking-wider">
                {t.hint}
              </div>
            </button>
          ))}
        </div>
      </section>

      {/* 通用 Agent 独立大入口（与场景卡视觉上区分） */}
      <section className="mb-12">
        <div className="flex items-center gap-3 mb-5">
          <span className="text-xs uppercase tracking-wider text-ink-2 font-medium">
            通用场景
          </span>
          <span className="flex-1 h-px bg-edge" />
        </div>

        <button
          onClick={() => {
            setCategory("general");
            // 不预填议题，让用户自己写
            setTimeout(() => {
              document
                .getElementById("custom-topic-input")
                ?.scrollIntoView({ behavior: "smooth", block: "center" });
            }, 0);
          }}
          className={`w-full text-left p-6 rounded-md border-2 transition-all bg-bg-surface relative overflow-hidden ${
            category === "general"
              ? "border-brand ring-1 ring-brand/30 bg-white"
              : "border-transparent hover:border-edge-strong hover:bg-white"
          }`}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-base font-medium text-ink">
                  通用议会
                </span>
                <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-ink/10 text-ink-2">
                  5 议员全出动
                </span>
              </div>
              <p className="text-sm text-ink-2 leading-relaxed">
                没有合适的固定场景？把任何决策放进来——
                个人选择、家庭议题、人际判断、要不要换城市、要不要冷处理某段关系……
                5 位议员会从理性 / 风险 / 机会 / 长期 / 反共识 五个角度跟你过一遍。
              </p>
            </div>
            <svg
              width="20" height="20" viewBox="0 0 24 24" fill="none"
              className="text-brand flex-shrink-0 mt-1"
            >
              <path
                d="M5 12h14M13 5l7 7-7 7"
                stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round"
              />
            </svg>
          </div>
        </button>
      </section>

      {/* 自定义输入 */}
      <section>
        <div
          id="custom-topic-input"
          className="flex items-center gap-3 mb-5"
        >
          <span className="text-xs uppercase tracking-wider text-ink-2 font-medium">
            {category === "general" ? "你的问题" : "或描述你自己的决策"}
          </span>
          <span className="flex-1 h-px bg-edge" />
        </div>

        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          rows={4}
          className="w-full p-5 bg-bg-surface border border-edge rounded-md text-ink placeholder:text-ink-3 focus:outline-none focus:border-brand focus:ring-1 focus:ring-brand/30 resize-none text-base leading-relaxed"
          placeholder="例：要不要接这个远程创业公司的 CTO offer？现金少 40%，给 2% 股权。"
        />

        <div className="mt-4">
          <label className="text-xs uppercase tracking-wider text-ink-2 font-medium mb-2 block">
            关键背景{" "}
            <span className="text-ink-3 normal-case lowercase tracking-normal">
              （选填）
            </span>
          </label>
          <textarea
            value={background}
            onChange={(e) => setBackground(e.target.value)}
            rows={3}
            className="w-full p-4 bg-bg-surface border border-edge rounded-md text-ink placeholder:text-ink-3 focus:outline-none focus:border-brand focus:ring-1 focus:ring-brand/30 resize-none text-sm leading-relaxed"
            placeholder="现职年薪、家庭负担、房贷、地理约束、健康状况……越具体议会越准。"
          />
        </div>

        <div className="mt-6 flex justify-end">
          <button
            onClick={handleSubmit}
            disabled={!topic.trim() || loading}
            className="px-8 py-3 bg-brand text-white rounded-md font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:bg-brand-hover transition flex items-center gap-2"
          >
            {loading ? (
              <>
                <Spinner /> 召集中
              </>
            ) : (
              <>召集议会 →</>
            )}
          </button>
        </div>
      </section>
    </div>
  );
}

function Spinner() {
  return (
    <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
  );
}
