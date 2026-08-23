"use client";

import { useEffect, useMemo, useState } from "react";
import { AgentTraceRow } from "@/components/AgentTraceRow";
import {
  AgentTraceEvent,
  getMember,
  MemberId,
  MemberStatus,
} from "@/lib/types";

interface Props {
  traces: AgentTraceEvent[];
  expertIds: MemberId[];
  activeMember?: MemberId | null;
  statuses?: Partial<Record<MemberId, MemberStatus>>;
}

export function AgentResearchTimeline({
  traces,
  expertIds,
  activeMember,
  statuses = {},
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const visible = useMemo(
    () => traces.filter((trace) => !activeMember || trace.agent_id === activeMember),
    [traces, activeMember]
  );
  const terminalIds = useMemo(
    () =>
      new Set<MemberId>([
        ...(Object.entries(statuses)
          .filter(([, status]) => status === "done" || status === "degraded")
          .map(([id]) => id) as MemberId[]),
        ...(
        traces
          .filter((trace) => ["completed", "degraded", "failed"].includes(trace.phase))
          .map((trace) => trace.agent_id)
        ),
      ]),
    [statuses, traces]
  );
  const allDone = expertIds.length > 0 && expertIds.every((id) => terminalIds.has(id));

  useEffect(() => {
    if (!allDone) setCollapsed(false);
  }, [allDone]);

  if (expertIds.length === 0 && traces.length === 0) return null;

  return (
    <section className="mx-4 mb-4 overflow-hidden rounded-xl border border-edge bg-bg-surface/80">
      <button
        type="button"
        onClick={() => setCollapsed((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div>
          <div className="text-sm font-medium text-ink">调研实况</div>
          <div className="mt-0.5 text-[11px] text-ink-3">
            {activeMember
              ? `正在查看${getMember(activeMember)?.display_name || "专家"}的研究路径`
              : allDone
              ? "已调研完成，即将开始辩论 · 可展开查看证据路径"
              : `${terminalIds.size}/${expertIds.length} 位专家正在调研`}
          </div>
        </div>
        <span className="text-xs text-ink-3">{collapsed ? "展开" : "收起"}</span>
      </button>

      {!collapsed && (
        <div className="border-t border-edge px-4 py-1">
          {visible.length > 0 ? (
            visible.slice(-24).map((event, index) => (
              <AgentTraceRow
                key={event.seq ?? `${event.trace_id}-${event.step}-${event.phase}-${index}`}
                event={event}
              />
            ))
          ) : (
            <div className="py-5 text-center text-xs text-ink-3">
              {terminalIds.size
                ? "已完成专家的观点正在下方依次展示"
                : "议会正在确定每位专家的调研任务…"}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
