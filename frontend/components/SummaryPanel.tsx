"use client";

import {
  AgentTraceEvent,
  ElectionResult,
  MemberId,
  ParliamentSummary,
  Stance,
  TranscriptEntry,
  getMember,
} from "@/lib/types";
import { ElectionDetail } from "./ElectionDetail";
import { AgentResearchTimeline } from "./AgentResearchTimeline";

interface Props {
  summary: ParliamentSummary;
  topic: string;
  durationSeconds?: number;
  /** 用户在议会里说过的话（V1-S1 临时从 sessionStorage 拿，S3 改为后端 archive） */
  userMessages?: TranscriptEntry[];
  agentTraces?: AgentTraceEvent[];
  selectedExpertIds?: MemberId[];
}

/**
 * 议会纪要：杂志式版面，左大标题 + 右内容，分隔线明显，无圆角卡片。
 */
export function SummaryPanel({
  summary,
  topic,
  durationSeconds,
  userMessages = [],
  agentTraces = [],
  selectedExpertIds = [],
}: Props) {
  const chair = summary.chair ? getMember(summary.chair) : undefined;

  return (
    <article className="max-w-3xl mx-auto px-8 py-16">
      {/* 杂志标题区 */}
      <header className="mb-16 pb-8 border-b-2 border-ink">
        <div className="text-xs uppercase tracking-widest text-ink-muted mb-3">
          议会纪要
          {durationSeconds !== undefined && (
            <>
              <span className="mx-2 text-ink-subtle">·</span>
              时长 {durationSeconds}s
            </>
          )}
        </div>
        <h1 className="font-serif text-4xl md:text-5xl font-medium text-ink leading-tight mb-4">
          {topic}
        </h1>
        {chair && (
          <div className="flex items-center gap-2 text-sm">
            <span
              className="inline-block w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: chair.color }}
            />
            <span className="text-ink-secondary">
              当选议长：
              <span className="font-medium" style={{ color: chair.color }}>
                {chair.display_name}
              </span>
              {summary.fallback_used && (
                <span className="text-ink-subtle ml-2 italic">
                  （兜底任命）
                </span>
              )}
            </span>
          </div>
        )}

        {/* V1-S1.2 议员最终立场分布 */}
        {summary.member_stances && (
          <MemberStanceBar stances={summary.member_stances} />
        )}
      </header>

      {selectedExpertIds.length > 0 && (
        <div className="mb-12 -mx-4">
          <AgentResearchTimeline
            traces={agentTraces}
            expertIds={selectedExpertIds}
          />
        </div>
      )}

      {summary.agent_results && (
        <MagazineSection
          label="Research Evidence"
          title="专家调研与证据"
          emptyHint="本次会议没有外部调研记录"
        >
          <div className="space-y-5">
            {Object.entries(summary.agent_results).map(([expertId, archive]) => {
              const expert = getMember(expertId);
              const result = archive.result;
              return (
                <div key={expertId} className="border-l-2 pl-4" style={{ borderColor: expert?.color }}>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="font-medium" style={{ color: expert?.color }}>
                      {expert?.display_name || expertId}
                    </span>
                    <span className="text-xs text-ink-3">
                      {archive.metrics?.tool_calls || 0} 次工具行动
                      {archive.metrics?.duration_ms !== undefined &&
                        ` · ${(archive.metrics.duration_ms / 1000).toFixed(1)}s`}
                    </span>
                    {result.degraded && (
                      <span className="rounded bg-stance-neutralBg px-1.5 py-0.5 text-[10px] text-stance-neutralText">
                        信息有限
                      </span>
                    )}
                  </div>
                  <p className="mt-1.5 text-sm leading-relaxed text-ink">{result.thesis}</p>
                  {archive.evidence && archive.evidence.length > 0 && (
                    <ul className="mt-2 space-y-1.5">
                      {archive.evidence.map((evidence) => (
                        <li key={evidence.evidence_id} className="text-xs leading-relaxed text-ink-2">
                          <span className="mr-1 text-ink-4">[{evidence.strength}]</span>
                          {evidence.claim_summary}
                          {evidence.source_url ? (
                            <a
                              href={evidence.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="ml-1 text-brand hover:underline"
                            >
                              — {evidence.source_domain || evidence.source_title}
                            </a>
                          ) : (
                            <span className="ml-1 text-ink-4">— {evidence.source_title}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </MagazineSection>
      )}

      {/* V1-S1.2 议长选举详情（嵌入到顶部议会信息之下） */}
      {summary.election && (
        <MagazineSection
          label="Election"
          title="议长选举"
          emptyHint="（未拿到选举数据）"
        >
          <ElectionDetail election={summary.election} />
        </MagazineSection>
      )}

      {/* 共识区 */}
      <MagazineSection
        label="Consensus"
        title="共识区"
        emptyHint="本次议会没有达成明显共识"
      >
        {summary.consensus?.map((c, i) => (
          <p key={i} className="text-ink leading-relaxed mb-3">
            {c}
          </p>
        ))}
      </MagazineSection>

      {/* 分歧区 */}
      <MagazineSection
        label="Dissent"
        title="分歧区"
        emptyHint="本次议会无明显分歧"
      >
        {summary.dissent?.map((d, i) => (
          <div key={i} className="mb-5">
            <h3 className="font-serif text-lg font-medium text-ink mb-2">
              {d.topic}
            </h3>
            <ul className="space-y-1 pl-4">
              {Object.entries(d.sides || {}).map(([mid, stance]) => {
                const m = getMember(mid);
                return (
                  <li
                    key={mid}
                    className="text-sm text-ink-secondary leading-relaxed"
                  >
                    <span
                      className="font-medium"
                      style={{ color: m?.color || "#374151" }}
                    >
                      {m?.display_name || mid}
                    </span>
                    <span className="text-ink-muted mx-1.5">——</span>
                    {stance}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </MagazineSection>

      {/* 盲区 */}
      <MagazineSection
        label="Blind Spots"
        title="议长补的盲区"
        emptyHint="议长未指出盲区"
      >
        {summary.blind_spots?.map((b, i) => (
          <p key={i} className="text-ink leading-relaxed mb-3">
            {b}
          </p>
        ))}
      </MagazineSection>

      {/* 综合建议：突出展示 */}
      <section className="my-12 p-6 border-l-4 border-brand bg-bg-elev/50">
        <div className="text-xs uppercase tracking-widest text-brand font-medium mb-3">
          Advice · 综合建议
        </div>
        <p className="text-ink leading-relaxed whitespace-pre-wrap">
          {summary.advice}
        </p>
      </section>

      {/* 未决问题 */}
      <MagazineSection
        label="Open Questions"
        title="未决问题"
        emptyHint="本次议会未留下未决问题"
      >
        {summary.open_questions?.map((q, i) => (
          <p key={i} className="text-ink leading-relaxed mb-3">
            {q}
          </p>
        ))}
      </MagazineSection>

      {/* V1-S1.4 用户在议会里说过的话 */}
      {userMessages.length > 0 && (
        <MagazineSection
          label="User Inputs"
          title="你在议会里说过的话"
          emptyHint=""
        >
          {userMessages.map((m, i) => (
            <p key={i} className="text-ink leading-relaxed mb-2 pl-3 border-l-2 border-me">
              {m.content}
            </p>
          ))}
        </MagazineSection>
      )}

      {/* 底部返回 */}
      <footer className="mt-16 pt-8 border-t border-edge text-center">
        <button
          onClick={() => window.location.assign("/")}
          className="px-6 py-2 border border-edge-strong text-ink-secondary hover:border-brand hover:text-brand transition text-sm"
        >
          ← 发起新议会
        </button>
      </footer>
    </article>
  );
}

/**
 * V1-S1.4：议员最终立场分布（5 色块 + 紧凑计数）
 */
function MemberStanceBar({
  stances,
}: {
  stances: Record<string, { stance: Stance; confidence: number }>;
}) {
  return (
    <div className="mt-5 pt-4 border-t border-edge">
      <div className="text-xs uppercase tracking-widest text-ink-subtle mb-2">
        议员立场分布
      </div>
      <div className="flex items-center gap-3 flex-wrap text-xs">
        {(["for", "against", "neutral"] as Stance[]).map((s) => {
          const members = Object.entries(stances).filter(
            ([, info]) => info.stance === s
          );
          if (members.length === 0) return null;
          const map = {
            for: { label: "支持", cls: "text-stance-forText" },
            against: { label: "反对", cls: "text-stance-againstText" },
            neutral: { label: "中立", cls: "text-stance-neutralText" },
          }[s];
          return (
            <div key={s} className="flex items-center gap-1.5">
              <span className={`font-medium ${map.cls}`}>{map.label}</span>
              <span className="text-ink-3">{members.length}</span>
              <span className="text-ink-3">·</span>
              {members.map(([mid], i) => {
                const m = getMember(mid);
                if (!m) return null;
                return (
                  <span key={mid} style={{ color: m.color }}>
                    {m.display_name}
                    {i < members.length - 1 ? " " : ""}
                  </span>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MagazineSection({
  label,
  title,
  emptyHint,
  children,
}: {
  label: string;
  title: string;
  emptyHint: string;
  children: React.ReactNode;
}) {
  const childArray = Array.isArray(children) ? children : [children];
  const hasContent = childArray.filter(Boolean).length > 0;

  return (
    <section className="mb-12">
      <div className="mb-4">
        <div className="text-xs uppercase tracking-widest text-ink-subtle mb-1">
          {label}
        </div>
        <h2 className="font-serif text-2xl font-medium text-ink">{title}</h2>
      </div>
      {hasContent ? (
        <div>{children}</div>
      ) : (
        <p className="text-ink-muted italic text-sm">{emptyHint}</p>
      )}
    </section>
  );
}
