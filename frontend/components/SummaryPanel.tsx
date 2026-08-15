"use client";

import {
  BasisLevel,
  ParliamentSummary,
  Stance,
  SummaryEvidence,
  TranscriptEntry,
  getMember,
} from "@/lib/types";

interface Props {
  summary: ParliamentSummary;
  topic: string;
  durationSeconds?: number;
  userMessages?: TranscriptEntry[];
}

const BASIS_LABEL: Record<BasisLevel, string> = {
  limited: "判断基础有限",
  moderate: "判断基础一般",
  strong: "判断基础较强",
};

const STRENGTH_LABEL = {
  conditional: "条件性建议",
  clear: "明确建议",
  insufficient: "信息仍不足",
};

export function SummaryPanel({
  summary,
  topic,
  durationSeconds,
  userMessages = [],
}: Props) {
  const recommendation = summary.recommendation || summary.advice;
  const basisLevel = summary.basis_assessment?.level || "limited";
  const nextAction = summary.next_actions?.[0] || summary.open_questions?.[0];

  return (
    <article className="mx-auto max-w-4xl px-5 py-10 sm:px-8 sm:py-16">
      <header className="mb-10 border-b-2 border-[#2d2830] pb-7">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs tracking-[0.14em] text-[#8b838b]">
          <span>DECISION COUNCIL · 议会结论</span>
          {summary.round_count !== undefined && (
            <span>· {summary.round_count} 轮正式讨论</span>
          )}
          {durationSeconds !== undefined && <span>· {durationSeconds}s</span>}
        </div>
        <h1 className="font-serif text-3xl font-medium leading-tight text-[#2d2830] sm:text-5xl">
          {topic}
        </h1>
      </header>

      <section className="mb-12 rounded-[22px] border border-[#ddcfd3] bg-[#fffdfb] p-6 shadow-[0_18px_55px_rgba(60,34,55,0.08)] sm:p-8">
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <span className="rounded-full bg-[#4b1c62] px-3 py-1 text-xs font-bold text-white">
            一分钟结论
          </span>
          <span className="rounded-full bg-[#fff0ed] px-3 py-1 text-xs font-bold text-[#a44343]">
            {STRENGTH_LABEL[summary.recommendation_strength || "insufficient"]}
          </span>
          <span className="rounded-full bg-[#f0ecef] px-3 py-1 text-xs font-bold text-[#675d67]">
            {BASIS_LABEL[basisLevel]}
          </span>
        </div>
        <p className="text-xl font-bold leading-relaxed text-[#2d2830] sm:text-2xl">
          {recommendation || "建议先补充关键信息，再作最终决定。"}
        </p>
        {summary.key_reason && (
          <div className="mt-6 border-l-2 border-[#ec403d] pl-4">
            <div className="text-xs font-bold tracking-wider text-[#a36b6a]">
              最关键理由
            </div>
            <p className="mt-1.5 leading-7 text-[#554d56]">
              {summary.key_reason}
            </p>
          </div>
        )}
        {nextAction && (
          <div className="mt-6 rounded-xl bg-[#f5f1f3] px-4 py-3">
            <span className="text-xs font-bold text-[#4b1c62]">下一步：</span>
            <span className="ml-2 text-sm text-[#554d56]">{nextAction}</span>
          </div>
        )}
      </section>

      {summary.member_stances && (
        <Section eyebrow="Council Positions" title="专家最终倾向">
          <MemberStanceBar stances={summary.member_stances} />
        </Section>
      )}

      <Section
        eyebrow="Consensus"
        title="已经形成的共识"
        emptyHint="本场会议尚未形成明确共识。"
      >
        <NumberedItems items={summary.consensus} />
      </Section>

      <Section
        eyebrow="Dissent"
        title="仍然存在的分歧"
        emptyHint="本场会议没有保留明显分歧。"
      >
        <div className="space-y-6">
          {summary.dissent?.map((item, index) => (
            <div key={`${item.topic}-${index}`} className="border-l-2 border-[#cdbfc5] pl-4">
              <h3 className="font-serif text-lg font-medium text-[#2d2830]">
                {item.topic}
              </h3>
              <ul className="mt-2 space-y-2">
                {Object.entries(item.sides || {}).map(([expertId, view]) => {
                  const expert = getMember(expertId);
                  return (
                    <li key={expertId} className="text-sm leading-6 text-[#625a63]">
                      <strong style={{ color: expert?.color }}>
                        {expert?.display_name || expertId}
                      </strong>
                      <span className="mx-2 text-[#b2a8ae]">—</span>
                      {view}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      </Section>

      {summary.decision_options && summary.decision_options.length > 0 && (
        <Section eyebrow="Options" title="方案收益与代价">
          <div className="grid gap-4 md:grid-cols-2">
            {summary.decision_options.map((item) => (
              <div key={item.option} className="rounded-xl border border-[#e2d9dc] bg-white p-4">
                <h3 className="font-bold text-[#352f36]">{item.option}</h3>
                <TradeoffList label="可能收益" items={item.benefits} />
                <TradeoffList label="主要代价" items={item.costs} />
              </div>
            ))}
          </div>
        </Section>
      )}

      {summary.analysis_basis && (
        <Section eyebrow="Analysis Basis" title="分析依据">
          <p className="mb-4 text-sm leading-6 text-[#766d75]">
            {summary.basis_assessment?.description ||
              `已整理 ${summary.basis_assessment?.verified_count || 0} 项可核实信息。`}
          </p>
          <div className="space-y-3">
            <EvidenceGroup title="用户提供的信息" items={summary.analysis_basis.user_facts} />
            <EvidenceGroup title="网页信息来源" items={summary.analysis_basis.web_sources} />
            <EvidenceGroup title="计算结果" items={summary.analysis_basis.calculations} />
            <EvidenceGroup title="推断假设" items={summary.analysis_basis.inferences} />
          </div>
        </Section>
      )}

      <Section
        eyebrow="Next Actions"
        title="建议你接下来做什么"
        emptyHint="本场会议没有留下额外行动。"
      >
        <NumberedItems items={summary.next_actions || summary.open_questions} />
      </Section>

      <Section
        eyebrow="Reversal Conditions"
        title="什么情况会改变这个结论"
        emptyHint="暂未识别明确的结论翻转条件。"
      >
        <NumberedItems items={summary.reversal_conditions} />
      </Section>

      {userMessages.length > 0 && (
        <Section eyebrow="User Inputs" title="你在会议中补充的信息">
          <div className="space-y-2">
            {userMessages.map((message, index) => (
              <p
                key={`${message.content}-${index}`}
                className="border-l-2 border-[#ec403d] pl-3 leading-7 text-[#625a63]"
              >
                {message.content}
              </p>
            ))}
          </div>
        </Section>
      )}

      <footer className="mt-14 border-t border-[#ded5d9] pt-8 text-center">
        <p className="mb-5 text-xs leading-5 text-[#928991]">
          这是辅助决策建议。最终选择仍应由你结合自身情况作出。
        </p>
        <button
          onClick={() => window.location.assign("/")}
          className="rounded-lg border border-[#a99ca4] px-6 py-2.5 text-sm font-bold text-[#554d56] transition hover:border-[#4b1c62] hover:text-[#4b1c62]"
        >
          ← 发起新议会
        </button>
      </footer>
    </article>
  );
}

function Section({
  eyebrow,
  title,
  emptyHint,
  children,
}: {
  eyebrow: string;
  title: string;
  emptyHint?: string;
  children: React.ReactNode;
}) {
  const empty =
    children === undefined ||
    children === null ||
    (Array.isArray(children) && children.length === 0);
  return (
    <section className="mb-12">
      <div className="mb-5">
        <div className="mb-1 text-xs font-bold uppercase tracking-[0.14em] text-[#a36b6a]">
          {eyebrow}
        </div>
        <h2 className="font-serif text-2xl font-medium text-[#2d2830]">{title}</h2>
      </div>
      {empty ? (
        <p className="text-sm italic text-[#928991]">{emptyHint}</p>
      ) : (
        children
      )}
    </section>
  );
}

function NumberedItems({ items = [] }: { items?: string[] }) {
  return (
    <ol className="space-y-3">
      {items.map((item, index) => (
        <li key={`${item}-${index}`} className="flex gap-3 leading-7 text-[#554d56]">
          <span className="mt-1 grid h-6 w-6 flex-none place-items-center rounded-full bg-[#f0e9ed] text-xs font-bold text-[#6f4878]">
            {index + 1}
          </span>
          <span>{item}</span>
        </li>
      ))}
    </ol>
  );
}

function TradeoffList({ label, items }: { label: string; items: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="mt-4">
      <div className="text-xs font-bold text-[#8d6674]">{label}</div>
      <ul className="mt-1.5 space-y-1 text-sm leading-6 text-[#625a63]">
        {items.map((item) => (
          <li key={item}>· {item}</li>
        ))}
      </ul>
    </div>
  );
}

function EvidenceGroup({
  title,
  items,
}: {
  title: string;
  items: SummaryEvidence[];
}) {
  if (!items?.length) return null;
  return (
    <details className="group rounded-xl border border-[#e2d9dc] bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-bold text-[#554d56]">
        <span>{title}</span>
        <span className="text-xs font-normal text-[#968b92]">
          {items.length}项 <span className="ml-1 group-open:hidden">＋</span>
          <span className="ml-1 hidden group-open:inline">－</span>
        </span>
      </summary>
      <ul className="space-y-3 border-t border-[#eee8eb] px-4 py-4">
        {items.map((item) => (
          <li key={item.evidence_id} className="text-sm leading-6 text-[#625a63]">
            <p>{item.content}</p>
            {item.calculation_formula && (
              <p className="mt-1 rounded bg-[#f5f1f3] px-2 py-1 font-mono text-xs text-[#675d67]">
                {item.calculation_formula}
              </p>
            )}
            {item.source_url && (
              <a
                href={item.source_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-xs font-bold text-[#6f4878] hover:underline"
              >
                {item.source_title || item.source_domain || "查看来源"} ↗
              </a>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

function MemberStanceBar({
  stances,
}: {
  stances: Record<string, { stance: Stance; confidence: number }>;
}) {
  const labels: Record<Stance, string> = {
    support: "建议推进",
    oppose: "不建议",
    cautious: "谨慎推进",
    neutral: "暂不判断",
    for: "支持",
    against: "反对",
  };
  return (
    <div className="flex flex-wrap gap-3">
      {Object.entries(stances).map(([expertId, info]) => {
        const expert = getMember(expertId);
        return (
          <div
            key={expertId}
            className="flex items-center gap-2 rounded-full border border-[#e2d9dc] bg-white px-3 py-2 text-xs"
          >
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: expert?.color || "#8b838b" }}
            />
            <strong style={{ color: expert?.color }}>
              {expert?.display_name || expertId}
            </strong>
            <span className="text-[#817780]">{labels[info.stance]}</span>
          </div>
        );
      })}
    </div>
  );
}
