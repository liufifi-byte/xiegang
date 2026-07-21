"use client";

import { AgentTraceEvent, getMember } from "@/lib/types";

const PHASE_ICON: Record<AgentTraceEvent["phase"], string> = {
  started: "●",
  deciding: "…",
  tool_started: "↗",
  tool_completed: "✓",
  evidence_added: "+",
  finalizing: "≋",
  completed: "✓",
  degraded: "△",
  failed: "!",
};

export function AgentTraceRow({ event }: { event: AgentTraceEvent }) {
  const member = getMember(event.agent_id);
  if (!member) return null;
  const isTerminal = ["completed", "degraded", "failed"].includes(event.phase);

  return (
    <div className="flex gap-3 py-2.5 animate-slide-in">
      <div className="relative flex flex-col items-center">
        <div
          className={`w-7 h-7 rounded-full flex items-center justify-center text-[11px] text-white ${
            event.phase === "deciding" || event.phase === "tool_started"
              ? "animate-pulse-ring"
              : ""
          }`}
          style={{ backgroundColor: member.color }}
          aria-hidden="true"
        >
          {PHASE_ICON[event.phase]}
        </div>
        {!isTerminal && <div className="w-px flex-1 min-h-2 bg-edge mt-1" />}
      </div>

      <div className="min-w-0 flex-1 pb-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-xs font-medium" style={{ color: member.color }}>
            {member.display_name}
          </span>
          <span className="text-sm text-ink leading-relaxed">
            {event.public_message}
          </span>
          {event.duration_ms !== null && event.duration_ms !== undefined && (
            <span className="text-[10px] text-ink-4">
              {(event.duration_ms / 1000).toFixed(1)}s
            </span>
          )}
          {event.cache_hit && (
            <span className="rounded bg-bg-elev px-1.5 py-0.5 text-[10px] text-ink-3">
              已复用资料
            </span>
          )}
        </div>

        {event.display_query && (
          <div className="mt-1.5 rounded-md border border-edge bg-white px-2.5 py-2 text-xs text-ink-2 break-words">
            <span className="text-ink-4 mr-1.5">搜索</span>
            {event.display_query}
          </div>
        )}

        {event.source_previews && event.source_previews.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {event.source_previews.map((source, index) => {
              const label = source.domain || source.title;
              return source.url ? (
                <a
                  key={`${source.url}-${index}`}
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="max-w-full truncate rounded-full border border-edge bg-white px-2 py-1 text-[10px] text-ink-2 hover:border-brand hover:text-brand"
                  title={source.title}
                >
                  {label}
                </a>
              ) : (
                <span
                  key={`${source.title}-${index}`}
                  className="max-w-full truncate rounded-full bg-bg-elev px-2 py-1 text-[10px] text-ink-3"
                >
                  {label}
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
