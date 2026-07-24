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

const SCENE_DEFAULT_QUESTIONS: Record<string, string> = {
  real_estate: "要不要买房？",
  offer: "要不要接这个 offer？",
  job_change: "要不要跳槽？",
  investment: "要不要做这笔投资？",
  entrepreneurship: "要不要创业？",
  gold: "要不要买黄金？",
  stocks: "要不要买这只股票？",
  buy_bag: "要不要买这个包？",
};

const GENERAL_EXAMPLES = [
  "要不要接受这份看起来稳定、但成长很慢的工作？",
  "现在买房，还是再等一年？",
  "该不该和合伙人继续做下去？",
];

const SECTION_TITLE_CLASS = "text-sm font-bold text-[#4b3e4d]";

export function HomeSceneTabs({ onSubmit, loading }: Props) {
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [sceneTopic, setSceneTopic] = useState("");
  const [background, setBackground] = useState("");
  const [generalQuestion, setGeneralQuestion] = useState("");

  const handleSceneClick = (category: string) => {
    setSelectedCategory(category);
    setSceneTopic(
      SCENE_DEFAULT_QUESTIONS[category] ||
        `要不要${TOPIC_TEMPLATES.find((item) => item.category === category)?.display_name || "做这个决定"}？`
    );
    window.setTimeout(() => {
      document
        .getElementById("scene-decision-form")
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  };

  const submitScene = () => {
    if (
      !selectedCategory ||
      !sceneTopic.trim() ||
      !background.trim() ||
      loading
    ) {
      return;
    }
    onSubmit(sceneTopic.trim(), selectedCategory, background.trim());
  };

  const submitGeneral = () => {
    if (!generalQuestion.trim() || loading) return;
    onSubmit(generalQuestion.trim(), "general", "");
  };

  return (
    <div className="max-w-3xl mx-auto px-5 sm:px-6 pb-28">
      <BrandHeader />

      <section className="mb-10">
        <SectionTitle>精选场景</SectionTitle>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {TOPIC_TEMPLATES.map((template) => (
            <button
              key={template.category}
              type="button"
              aria-pressed={selectedCategory === template.category}
              onClick={() => handleSceneClick(template.category)}
              className={`px-5 py-4 rounded-xl border text-left transition-all text-base font-medium ${
                selectedCategory === template.category
                  ? "border-[#4b1c62] bg-[#f3e9f3] text-[#30103f] ring-1 ring-[#4b1c62]/20"
                  : "border-[#e9ded8] bg-[#fffdfb] text-[#251536] hover:-translate-y-0.5 hover:border-[#4b1c62]/45 hover:shadow-md"
              }`}
            >
              {template.display_name}
            </button>
          ))}
        </div>
      </section>

      {selectedCategory && (
        <section id="scene-decision-form" className="mb-12 animate-slide-in">
          <div className="mb-5">
            <label htmlFor="scene-topic" className={`${SECTION_TITLE_CLASS} mb-3 block`}>
              描述你的决策
            </label>
            <textarea
              id="scene-topic"
              value={sceneTopic}
              onChange={(event) => setSceneTopic(event.target.value)}
              rows={3}
              className="w-full min-h-28 resize-none rounded-xl border border-[#e9ded8] bg-white px-4 py-3.5 text-base leading-relaxed text-[#251536] placeholder:text-[#aaa0a6] focus:border-[#4b1c62] focus:outline-none focus:ring-4 focus:ring-[#4b1c62]/10"
            />
          </div>

          <div>
            <label htmlFor="scene-background" className={`${SECTION_TITLE_CLASS} mb-3 block`}>
              关键背景
            </label>
            <textarea
              id="scene-background"
              value={background}
              onChange={(event) => setBackground(event.target.value)}
              rows={4}
              className="w-full min-h-32 resize-none rounded-xl border border-[#e9ded8] bg-white px-4 py-3.5 text-sm leading-relaxed text-[#251536] placeholder:text-[#aaa0a6] focus:border-[#4b1c62] focus:outline-none focus:ring-4 focus:ring-[#4b1c62]/10"
              placeholder="请补充会影响决定的事实：你的目标、预算或收入、时间限制、家庭与地理约束、目前已有方案，以及你最担心的结果。"
            />
          </div>

          <div className="mt-5 flex justify-end">
            <PrimaryButton
              onClick={submitScene}
              disabled={!sceneTopic.trim() || !background.trim() || Boolean(loading)}
              loading={loading}
            />
          </div>
        </section>
      )}

      <section>
        <SectionTitle>通用场景</SectionTitle>
        <div className="rounded-[20px] border border-[#5b3052]/15 bg-white/85 px-5 py-5 sm:px-6 shadow-[0_24px_70px_rgba(54,28,53,0.12)] backdrop-blur">
          <label htmlFor="general-question" className={`${SECTION_TITLE_CLASS} mb-3 block`}>
            你想和大家讨论什么？
          </label>
          <textarea
            id="general-question"
            value={generalQuestion}
            onChange={(event) => setGeneralQuestion(event.target.value)}
            rows={3}
            maxLength={240}
            className="w-full min-h-28 resize-none rounded-xl border border-[#e9ded8] bg-[#fffdfb] px-4 py-3.5 text-base leading-relaxed text-[#251536] placeholder:text-[#aaa0a6] focus:border-[#4b1c62] focus:outline-none focus:ring-4 focus:ring-[#4b1c62]/10"
            placeholder="例如：我现在该不该辞职，去做一个还没有收入的独立项目？"
          />
          <div className="mt-3.5 flex items-center justify-between gap-4">
            <span className="text-xs text-[#998e96]" aria-live="polite">
              {generalQuestion.length} / 240
            </span>
            <PrimaryButton
              onClick={submitGeneral}
              disabled={!generalQuestion.trim() || Boolean(loading)}
              loading={loading}
            />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap justify-center gap-2" aria-label="示例问题">
          {GENERAL_EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => setGeneralQuestion(example)}
              className="max-w-full rounded-full border border-[#eadfd9] bg-white/60 px-3 py-2 text-left text-xs text-[#71646e] transition hover:border-[#ec403d]/55 hover:text-[#ec403d]"
            >
              {example}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function BrandHeader() {
  return (
    <header className="pt-10 sm:pt-14 pb-10 text-center">
      <img
        src="/brand/xiegang-logo.png"
        alt="斜杠"
        width={1150}
        height={520}
        className="mx-auto block h-auto w-[260px] max-w-[82vw] sm:w-[310px] mix-blend-multiply"
      />
      <p className="mt-1 text-xs font-bold tracking-[0.14em] text-[#ec403d]">
        DECISION COUNCIL
      </p>
      <p className="mt-3 font-serif text-xl text-[#251536] sm:text-2xl">
        先<span className="mx-1 font-bold text-[#ec403d]">杠</span>一杠，再下结论
      </p>
    </header>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-3">
      <h2 className={SECTION_TITLE_CLASS}>{children}</h2>
      <span className="h-px flex-1 bg-[#e9ded8]" />
    </div>
  );
}

function PrimaryButton({
  onClick,
  disabled,
  loading,
}: {
  onClick: () => void;
  disabled: boolean;
  loading?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex min-h-11 items-center justify-center gap-2 rounded-[10px] bg-gradient-to-br from-[#30103f] to-[#4b1c62] px-5 font-bold text-white shadow-[0_9px_18px_rgba(60,20,78,0.18)] transition hover:-translate-y-0.5 hover:from-[#431456] hover:to-[#68227d] disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none disabled:hover:translate-y-0"
    >
      {loading ? (
        <>
          <Spinner /> 正在准备
        </>
      ) : (
        <>选择讨论者 →</>
      )}
    </button>
  );
}

function Spinner() {
  return (
    <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
  );
}
