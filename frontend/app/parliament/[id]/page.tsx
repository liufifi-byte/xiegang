"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ChatMessage } from "@/components/ChatMessage";
import { UserBubble } from "@/components/UserBubble";
import { SystemMessage } from "@/components/SystemMessage";
import { ChatInput } from "@/components/ChatInput";
import { AgentResearchTimeline } from "@/components/AgentResearchTimeline";
import {
  CouncilPhase,
  CouncilPhaseBar,
} from "@/components/council/CouncilPhaseBar";
import { ExpertSeatPanel } from "@/components/council/ExpertSeatPanel";
import { ClarificationModal } from "@/components/council/ClarificationModal";
import {
  getParliament,
  heartbeatParliament,
  submitClarification,
} from "@/lib/api";
import { subscribeParliament } from "@/lib/sse";
import {
  AgentTraceEvent,
  ClarificationQuestion,
  MemberId,
  MemberStatus,
  ParliamentSummary,
  TranscriptEntry,
  getMember,
} from "@/lib/types";

export default function ParliamentPage() {
  const params = useParams();
  const router = useRouter();
  const parliamentId = params.id as string;

  const [topic, setTopic] = useState("");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [toolCalls, setToolCalls] = useState<any[]>([]);
  const [selectedExpertIds, setSelectedExpertIds] = useState<MemberId[]>([]);
  const [agentTraces, setAgentTraces] = useState<AgentTraceEvent[]>([]);
  const [memberStatus, setMemberStatus] = useState<
    Partial<Record<MemberId, MemberStatus>>
  >({});
  const [currentRound, setCurrentRound] = useState(0);
  const [status, setStatus] = useState<
    "connecting" | "running" | "awaiting_input" | "done" | "error"
  >("connecting");
  const [summary, setSummary] = useState<ParliamentSummary | null>(null);
  const [duration, setDuration] = useState<number | undefined>(undefined);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [workerAvailable, setWorkerAvailable] = useState(true);
  const [serverPhase, setServerPhase] = useState<string>("queued");
  const [coreDisputes, setCoreDisputes] = useState<
    NonNullable<ParliamentSummary["core_disputes"]>
  >([]);
  const [clarificationQuestions, setClarificationQuestions] = useState<
    ClarificationQuestion[]
  >([]);

  // 用户选中的议员（右侧详情抽屉）
  const [activeMember, setActiveMember] = useState<MemberId | null>(null);

  // 用户的插话（MVP: 本地追加，不写后端）
  const [userMessages, setUserMessages] = useState<TranscriptEntry[]>([]);

  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  const transcriptScrollRef = useRef<HTMLDivElement | null>(null);
  const autoFollowRef = useRef(true);
  const initialScrollDoneRef = useRef(false);
  const [hasNewMessages, setHasNewMessages] = useState(false);

  // 合并 transcript：
  //   开场白（"开始调研"）—— 在议员**还没拿到研究结果**时显示
  //   服务端 research entry —— 拿到时展示完整结论，替换开场白
  //   debate / election / 用户消息 —— 按时间顺序追加
  const mergedTranscript = useMemo(
    () => [...transcript, ...userMessages],
    [transcript, userMessages]
  );

  // 初次进入定位最新消息；用户向上阅读后不再抢夺滚动位置。
  useEffect(() => {
    const container = transcriptScrollRef.current;
    if (!container) return;
    const frame = window.requestAnimationFrame(() => {
      if (!initialScrollDoneRef.current) {
        container.scrollTop = container.scrollHeight;
        initialScrollDoneRef.current = true;
        return;
      }
      if (autoFollowRef.current) {
        container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
      } else {
        setHasNewMessages(true);
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [agentTraces.length, mergedTranscript.length]);

  const handleTranscriptScroll = () => {
    const container = transcriptScrollRef.current;
    if (!container) return;
    const nearBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight < 96;
    autoFollowRef.current = nearBottom;
    if (nearBottom) setHasNewMessages(false);
  };

  const jumpToLatest = () => {
    const container = transcriptScrollRef.current;
    if (!container) return;
    autoFollowRef.current = true;
    setHasNewMessages(false);
    container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
  };

  useEffect(() => {
    if (!parliamentId) return;
    let active = true;
    getParliament(parliamentId)
      .then((snapshot) => {
        if (!active) return;
        setTopic(snapshot.topic || "");
        setTranscript(snapshot.transcript || []);
        setToolCalls(snapshot.tool_calls || []);
        setAgentTraces(snapshot.agent_traces || []);
        setWorkerAvailable(snapshot.worker_available !== false);
        setServerPhase(snapshot.current_phase || snapshot.status || "queued");
        setClarificationQuestions(snapshot.clarification_questions || []);
        const ids = (snapshot.selected_expert_ids || []) as MemberId[];
        setSelectedExpertIds(ids);
        const snapshotMembers = snapshot.members_state || {};
        setMemberStatus(
          Object.fromEntries(
            ids.map((id) => [
              id,
              snapshotMembers[id]?.degraded
                ? "degraded"
                : snapshotMembers[id]
                ? "done"
                : "idle",
            ])
          ) as Partial<Record<MemberId, MemberStatus>>
        );
        if (snapshot.status === "done") setStatus("done");
        else if (snapshot.status === "awaiting_input") {
          setStatus("awaiting_input");
          setServerPhase("awaiting_input");
        }
        else if (snapshot.status === "failed" || snapshot.status === "cancelled") {
          setStatus("error");
          setErrorMsg(
            snapshot.status === "cancelled" ? "议会已被取消" : "议会运行失败"
          );
        } else if (snapshot.status === "running") setStatus("running");
      })
      .catch(() => {
        // SSE remains the live fallback.
      });
    return () => {
      active = false;
    };
  }, [parliamentId]);

  useEffect(() => {
    if (!parliamentId || status === "done" || status === "error") return;
    heartbeatParliament(parliamentId).catch(() => {});
    const timer = window.setInterval(
      () => heartbeatParliament(parliamentId).catch(() => {}),
      5_000
    );
    return () => window.clearInterval(timer);
  }, [parliamentId, status]);

  // === SSE 订阅 ===
  useEffect(() => {
    if (!parliamentId) return;

    const unsubscribe = subscribeParliament(
      parliamentId,
      (event) => {
        if (event.type === "parliament_started") {
          setStatus("running");
          setServerPhase("claimed");
          if (event.topic) setTopic(event.topic);
          return;
        }

        if (event.type === "clarification_requested") {
          setClarificationQuestions(event.questions || []);
          setStatus("awaiting_input");
          setServerPhase("awaiting_input");
          return;
        }

        if (
          event.type === "clarification_answered" ||
          event.type === "clarification_skipped"
        ) {
          setClarificationQuestions([]);
          setStatus("connecting");
          setServerPhase("queued");
          if (event.entry) {
            setTranscript((current) =>
              current.some(
                (item) =>
                  item.type === "user_clarification" &&
                  item.content === event.entry?.content
              )
                ? current
                : [...current, event.entry as TranscriptEntry]
            );
          }
          return;
        }

        if (event.type === "clarification_resumed") {
          setStatus("connecting");
          setServerPhase("queued");
          return;
        }

        if (
          event.type === "expert_completed" &&
          event.expert_id &&
          event.member &&
          event.entry
        ) {
          const expertId = event.expert_id as MemberId;
          const completedMember = event.member;
          const completedEntry = event.entry;
          setServerPhase("researching");
          setMemberStatus((prev) => ({
            ...prev,
            [expertId]: completedMember.degraded ? "degraded" : "done",
          }));
          setTranscript((prev) => {
            if (
              prev.some(
                (item) =>
                  item.type === "research" && item.speaker === expertId
              )
            ) {
              return prev;
            }
            return [...prev, completedEntry as TranscriptEntry];
          });
          return;
        }

        if (
          event.type === "agent_trace" &&
          event.trace_id &&
          event.run_id &&
          event.agent_id &&
          event.phase &&
          event.public_message &&
          event.timestamp !== undefined
        ) {
          const trace: AgentTraceEvent = {
            seq: event.seq,
            trace_id: event.trace_id,
            run_id: event.run_id,
            agent_id: event.agent_id,
            step: event.step || 0,
            phase: event.phase,
            public_message: event.public_message,
            public_intent: event.public_intent,
            tool_name: event.tool_name,
            display_query: event.display_query,
            source_previews: event.source_previews || [],
            evidence_count: event.evidence_count,
            duration_ms: event.duration_ms,
            cache_hit: event.cache_hit,
            timestamp: event.timestamp,
          };
          setAgentTraces((prev) => {
            if (trace.seq !== undefined && prev.some((item) => item.seq === trace.seq)) {
              return prev;
            }
            return [...prev, trace];
          });
          const statusByPhase: Partial<Record<AgentTraceEvent["phase"], MemberStatus>> = {
            started: "thinking",
            deciding: "thinking",
            tool_started: "using_tool",
            tool_completed: "organizing",
            evidence_added: "organizing",
            finalizing: "organizing",
            completed: "done",
            degraded: "degraded",
            failed: "error",
          };
          setMemberStatus((prev) => ({
            ...prev,
            [trace.agent_id]: statusByPhase[trace.phase] || "thinking",
          }));
          return;
        }

        if (event.type === "node_complete") {
          const payload = event.payload || {};
          const node = event.node;
          const phaseByNode: Record<string, string> = {
            parse_topic: "claimed",
            select_experts: "claimed",
            clarification_gate: "claimed",
            persist_clarification: "awaiting_input",
            research: "researching",
            opening_round: "cross_examining",
            extract_disputes: "cross_examining",
            cross_examination_round: "cross_examining",
            assess_third_round: "cross_examining",
            convergence_round: "cross_examining",
            summary: "summarizing",
          };
          if (node && phaseByNode[node]) setServerPhase(phaseByNode[node]);
          if (
            node === "persist_clarification" &&
            payload.clarification_waiting
          ) {
            setClarificationQuestions(
              (payload.clarification_questions || []) as ClarificationQuestion[]
            );
            setStatus("awaiting_input");
          }

          if (node === "select_experts" && Array.isArray(payload.selected_expert_ids)) {
            const ids = payload.selected_expert_ids as MemberId[];
            setSelectedExpertIds(ids);
            setMemberStatus(
              Object.fromEntries(ids.map((id) => [id, "idle"])) as Partial<
                Record<MemberId, MemberStatus>
              >
            );
          }

          // 只有正式调研开始后才把专家切到思考中；信息门槛期间保持待命。
          if (node === "research") {
            setMemberStatus((prev) => {
              const next = { ...prev };
              (Object.keys(next) as MemberId[]).forEach((k) => {
                if (next[k] === "idle" || next[k] === "done") {
                  // idle → 转为 thinking（保留 done，避免覆盖刚完成的）
                  if (next[k] === "idle") next[k] = "thinking";
                }
              });
              return next;
            });
          }

          // 议员完成调研：替换开场白 + 状态 done
          if (payload.members && typeof payload.members === "object") {
            setMemberStatus((prev) => {
              const next = { ...prev };
              (Object.keys(payload.members!) as MemberId[]).forEach((k) => {
                next[k] = payload.members?.[k]?.degraded ? "degraded" : "done";
              });
              return next;
            });
          }

          if (payload.transcript) {
            setTranscript(payload.transcript as TranscriptEntry[]);
          }
          if (Array.isArray(payload.core_disputes)) {
            setCoreDisputes(
              payload.core_disputes as NonNullable<
                ParliamentSummary["core_disputes"]
              >
            );
          }
          // V1-S4 接收工具调用轨迹
          if (payload.tool_calls) {
            setToolCalls(payload.tool_calls as any[]);
          }

          if (payload.current_round !== undefined) {
            const prevRound = currentRound;
            setCurrentRound(payload.current_round);

            // 阶段切换：从调研(R0)进入 R1 → 系统消息
            if (
              (
                node === "opening_round" ||
                node === "cross_examination_round" ||
                node === "convergence_round"
              ) &&
              payload.current_round > prevRound
            ) {
              setMemberStatus((prev) =>
                Object.fromEntries(
                  Object.keys(prev).map((id) => [id, "speaking"])
                ) as Partial<Record<MemberId, MemberStatus>>
              );
            }
          }

          if (payload.summary) {
            setSummary(payload.summary as ParliamentSummary);
          }
          if (payload.total_duration_seconds !== undefined) {
            setDuration(payload.total_duration_seconds);
          }
          return;
        }

        if (event.type === "parliament_done") {
          setStatus("done");
          return;
        }
        if (event.type === "cancelled") {
          setStatus("error");
          setErrorMsg("议会已被取消");
          return;
        }
        if (event.type === "error") {
          setStatus("error");
          setErrorMsg(event.error || "未知错误");
        }
      },
      // Codex 建议 / PRD fix: SSE 断开不等于会议崩了!
      // Worker 进程独立跑议会的(不依赖 SSE 存活), 所以 SSE 连接失败时
      // 不能 setStatus("error") 把议会判死刑 — 改为显示"重连中"温和提示.
      // 用户看到的是"网络不稳定,正在重连"而不是"会议已停止".
      () => {
        // sse.ts 内部已无限重连,只有 MAX_RECONNECT(60次) 才回调这里
        // 到这里说明确实长时间连不上(30分钟),才真的降级为 error
        setStatus("error");
        setErrorMsg("网络持续不稳定，会议可能仍在后台运行。请刷新页面查看最新状态。");
      }
    );
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parliamentId]);

  // ============================
  // Codex 建议 / PRD fix: SSE 可以断,会议不能跟着断
  // HTTP 轮询 fallback — 在非 done/error 状态下每 15s polling 一次
  // 这样即使 SSE 完全挂了,用户刷新页面或等一会儿也能看到最新状态
  // Worker 进程跑议会完全独立于 SSE, events 表在持续写入
  //
  // Codex P0-4: polling 必须完整恢复 transcript / members / traces / round / summary
  // ============================
  const statusRef = useRef(status);
  statusRef.current = status;
  useEffect(() => {
    if (!parliamentId) return;
    let active = true;
    const interval = setInterval(async () => {
      const cur = statusRef.current;
      if (!active || cur === "done" || cur === "error") return;
      try {
        const { getParliament } = await import("@/lib/api");
        const snapshot = await getParliament(parliamentId);
        if (!active) return;
        if (snapshot.status === "done") {
          setStatus("done");
          if (snapshot.summary) setSummary(snapshot.summary);
          if (snapshot.transcript) setTranscript(snapshot.transcript);
          if (snapshot.round) setCurrentRound(snapshot.round);
          return;
        }
        if (snapshot.status === "failed" || snapshot.status === "cancelled") {
          setStatus("error");
          setErrorMsg(snapshot.status === "cancelled" ? "议会已被取消" : "议会运行失败");
          return;
        }
        if (snapshot.status === "awaiting_input") {
          setStatus("awaiting_input");
          setServerPhase("awaiting_input");
        }
        if (snapshot.members_state) {
          setMemberStatus(
            Object.fromEntries(
              Object.entries(snapshot.members_state).map(([k, v]) => [
                k as MemberId,
                ((v as { degraded?: boolean })?.degraded ? "degraded" : "done") as MemberStatus,
              ])
            ) as Partial<Record<MemberId, MemberStatus>>
          );
        }
      } catch {
        // 静默失败,不打断用户
      }
    }, 20000); // 20s polling
    return () => {
      active = false;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parliamentId, status]);

  // ============================
  // 离开页面自动取消会议(用户需求)
  // 组件卸载(返回上一页/关页面/路由跳转)时主动调 cancel API
  // 只有议会还在(non-terminal)状态时才取消
  // 用 fetch keepalive 确保即使页面正在关闭请求也能发出
  // ============================
  useEffect(() => {
    return () => {
      const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "";
      fetch(`${apiBase}/api/parliament/${parliamentId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
      }).catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parliamentId]);

  const handleUserSubmit = async (text: string) => {
    // V1-S3: 用户插话真入后端 runtime_context，下一轮 debate 节点会读出来
    // 同时乐观更新本地展示，让用户立刻看到气泡
    const newMsg: TranscriptEntry = {
      speaker: "user",
      type: "user_interrupt",
      content: text,
      round: currentRound,
      timestamp: "刚刚",
    };
    setUserMessages((prev) => [...prev, newMsg]);

    // 真发到后端
    try {
      const { interruptParliament } = await import("@/lib/api");
      await interruptParliament(parliamentId, text);
    } catch (e) {
      // 乐观 UI 已显示，失败时给最后一条加 missing 标记
      console.warn("interrupt 请求失败（仅 UI 显示，后端不会接住）:", e);
      // 不弹错误框，避免打断用户。失败就是失败。
    }
  };

  const handleCancel = async () => {
    if (status === "done" || status === "error") return;
    // 乐观切换状态
    setStatus("error");
    setErrorMsg("议会已被用户取消");
    try {
      const { cancelParliament } = await import("@/lib/api");
      await cancelParliament(parliamentId);
    } catch (e) {
      console.warn("cancel 请求失败（SSE 仍可能在跑）:", e);
    }
  };

  const handleClarificationSubmit = async (
    answers: Array<{ question_id: string; value: string }>
  ) => {
    await submitClarification(parliamentId, "answer", answers);
    setClarificationQuestions([]);
    setStatus("connecting");
    setServerPhase("queued");
  };

  const handleClarificationSkip = async () => {
    await submitClarification(parliamentId, "skip");
    setClarificationQuestions([]);
    setStatus("connecting");
    setServerPhase("queued");
  };

  const handleReturnHome = async () => {
    try {
      await heartbeatParliament(parliamentId, true);
    } finally {
      router.push("/");
    }
  };

  const activeMemberData = activeMember
    ? {
        member: getMember(activeMember),
        status: memberStatus[activeMember] || "idle",
        latestEntry: [...transcript]
          .reverse()
          .find((e) => e.speaker === activeMember),
        toolCalls: toolCalls.filter((c) => c.member === activeMember),
        traces: agentTraces.filter((trace) => trace.agent_id === activeMember),
      }
    : null;

  const doneCount = useMemo(
    () =>
      Object.values(memberStatus).filter(
        (s) => s === "done" || s === "degraded"
      ).length,
    [memberStatus]
  );

  const phase: CouncilPhase = useMemo(() => {
    if (summary) return "summary";
    if (serverPhase === "summarizing" || serverPhase === "done") return "summary";
    if (serverPhase === "cross_examining") return "debate";
    if (serverPhase === "researching") return "research";
    if (currentRound > 0) return "debate";
    if (
      agentTraces.length > 0 ||
      Object.values(memberStatus).some((value) => value !== "idle")
    ) {
      return "research";
    }
    return "understanding";
  }, [agentTraces.length, currentRound, memberStatus, serverPhase, summary]);

  const phaseTitle = {
    understanding:
      serverPhase === "awaiting_input"
        ? "等待你补充关键信息"
        : "正在理解你的问题",
    research: "专家正在独立判断并核实事实",
    debate: currentRound > 0
      ? `专家正在进行第 ${Math.min(currentRound + 1, 3)} 轮讨论`
      : "专家正在形成核心争议",
    summary: "中立主持人正在形成会议结论",
  }[phase];

  // 完成时跳转到独立 summary 路由
  // V1-S1.1 修复 #1：之前内联渲染 SummaryPanel，刷新就丢
  // V1-S3: 用户插话已通过 interrupt API 真入 runtime_context，会随 debate 写入 transcript，
  //        summary 页从 transcript 抽 user_interrupt 即可，不再用 sessionStorage hack
  useEffect(() => {
    if (status !== "done" || !parliamentId) return;

    // 给后端 archive 一点时间把 summary 写入 DB（避免 summary 页 404）
    const t = setTimeout(() => {
      router.replace(`/summary/${parliamentId}`);
    }, 800);
    return () => clearTimeout(t);
  }, [status, parliamentId, userMessages, router]);

  // done 但 summary 还没到 → 显示加载态
  if (status === "done") {
    return (
      <main className="min-h-screen flex items-center justify-center">
        <div className="text-center">
          <div className="inline-block w-6 h-6 border-2 border-edge border-t-brand rounded-full animate-spin mb-3" />
          <div className="text-sm text-ink-3">正在加载议会纪要...</div>
        </div>
      </main>
    );
  }

  return (
    <main className="grid h-dvh min-h-0 grid-cols-[58px_minmax(0,1fr)] overflow-hidden bg-[#f5f3f1] text-[#2d2830] md:grid-cols-[64px_190px_minmax(0,1fr)] lg:grid-cols-[72px_220px_minmax(420px,1fr)_210px]">
      <aside className="flex flex-col items-center gap-4 bg-[#29212d] px-2 py-5">
        <img
          src="/brand/xiegang-logo.webp"
          alt="斜杠"
          loading="lazy"
          className="h-8 w-12 object-cover object-center brightness-[1.7] saturate-75 mix-blend-screen"
        />
        <button
          type="button"
          onClick={handleReturnHome}
          title="返回首页"
          className="group relative grid h-10 w-10 place-items-center rounded-xl text-[#cec7d0] transition hover:bg-[#493351] hover:text-white"
          aria-label="返回首页"
        >
          <svg
            viewBox="0 0 24 24"
            aria-hidden="true"
            className="h-5 w-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="m15 18-6-6 6-6" />
            <path d="M9 12h10" />
          </svg>
          <span className="pointer-events-none absolute left-12 z-40 hidden whitespace-nowrap rounded-md bg-[#201923] px-2 py-1 text-[11px] font-bold text-white shadow-lg group-hover:block">
            返回首页
          </span>
        </button>
      </aside>

      <aside className="hidden border-r border-[#e0dce0] bg-[#eeeceb] px-3 py-6 md:block">
        <p className="text-[11px] font-extrabold tracking-[0.12em] text-[#ec403d]">
          正在讨论
        </p>
        <div className="mt-4 flex items-center gap-2 rounded-xl bg-white p-3">
          <span className="grid h-9 w-9 place-items-center rounded-[10px] bg-gradient-to-br from-[#5a246b] to-[#e34a47] text-xs font-extrabold text-white">
            杠
          </span>
          <span className="min-w-0">
            <strong className="block truncate text-xs">本次议会</strong>
            <small className="mt-1 block text-[11px] text-[#88808a]">
              {selectedExpertIds.length || 5} 位专家
            </small>
          </span>
        </div>
        <div className="mt-6 rounded-xl border border-[#e2d9dc] bg-white/55 p-3">
          <p className="text-[11px] text-[#8b838b]">当前阶段</p>
          <p className="mt-1 text-sm font-bold text-[#4b1c62]">{phaseTitle}</p>
        </div>
        <p className="mx-1 mt-6 text-xs leading-6 text-[#8b838b]">
          你可以随时补充背景、追问某位专家，或结束本次会议。
        </p>
      </aside>

      <section className="grid min-h-0 min-w-0 grid-rows-[auto_auto_auto_minmax(0,1fr)_auto] overflow-hidden bg-[#f8f7f6]">
        <header className="flex items-center justify-between border-b border-[#e8e3e5] bg-white px-4 py-3.5 sm:px-7">
          <div className="min-w-0">
            <p className="text-[10px] font-extrabold tracking-[0.12em] text-[#ec403d]">
              第三步 · 专家议会
            </p>
            <h1 className="mt-1 truncate text-base font-bold text-[#2d2830] sm:text-lg">
              {phaseTitle}
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => selectedExpertIds[0] && setActiveMember(selectedExpertIds[0])}
              className="hidden text-xs font-bold text-[#b34b58] sm:block"
            >
              查看专家
            </button>
            {status === "connecting" ||
            status === "running" ||
            status === "awaiting_input" ? (
              <button
                onClick={handleCancel}
                className="rounded-lg bg-[#fff0ed] px-3 py-2 text-xs font-bold text-[#a44343] transition hover:bg-[#f9ded9]"
              >
                叫停
              </button>
            ) : (
              <span className="text-xs text-[#978994]">
                {status === "error" ? "已停止" : "已完成"}
              </span>
            )}
          </div>
        </header>

        <CouncilPhaseBar phase={phase} />
        <ExpertSeatPanel
          compact
          expertIds={selectedExpertIds}
          statuses={memberStatus}
          activeMember={activeMember}
          onSelect={(id) => setActiveMember(id)}
        />

        <div className="flex items-start gap-3 border-b border-[#e8e3e5] bg-[#fcfbfa] px-4 py-2.5 text-xs text-[#655d66] sm:px-7">
          <span className="rounded bg-[#fff0ed] px-2 py-1 text-[10px] font-bold text-[#9c5550]">
            议题
          </span>
          <p className="line-clamp-2 leading-5">{topic || "正在读取议题…"}</p>
        </div>

        <div className="relative min-h-0 overflow-hidden">
        <div
          ref={transcriptScrollRef}
          onScroll={handleTranscriptScroll}
          className="h-full min-h-0 overflow-y-auto overscroll-contain px-0 py-4 sm:px-4"
        >
          <SystemMessage text={`你已加入「${topic || "本次议会"}」`} />

          {coreDisputes.length > 0 && (
            <CoreDisputePanel disputes={coreDisputes} />
          )}

          {agentTraces.length > 0 && (
            <details className="mx-4 mb-4 rounded-xl border border-[#e4dce0] bg-white/70">
              <summary className="cursor-pointer px-4 py-3 text-xs font-bold text-[#6f6470]">
                Agent 运行记录
                <span className="ml-2 font-normal text-[#9b9199]">
                  技术展示，默认收起
                </span>
              </summary>
              <AgentResearchTimeline
                traces={agentTraces}
                expertIds={selectedExpertIds}
                activeMember={activeMember}
                statuses={memberStatus}
              />
            </details>
          )}

          {status === "connecting" && (
            <div className="mx-4 my-4 rounded-xl border border-[#eadfd9] bg-[#fffaf6] px-4 py-3 text-xs leading-5 text-[#71666e]">
              <strong className="text-[#4b1c62]">
                {!workerAvailable
                  ? "分析服务暂时不可用"
                  : selectedExpertIds.length
                  ? `${selectedExpertIds.length} 位专家已入席`
                  : "正在读取专家席"}
              </strong>
              <span className="ml-2">
                {workerAvailable
                  ? "已进入队列，正在等待分析服务领取…"
                  : "会议已经保存，服务恢复后会继续处理。"}
              </span>
            </div>
          )}

          {mergedTranscript.map((entry, i) => {
            if (entry.speaker === "user") {
              return <UserBubble key={`u-${i}`} entry={entry} />;
            }
            if (entry.speaker === "system" || entry.type === "election") {
              return <SystemMessage key={`s-${i}`} text={entry.content} />;
            }
            return <ChatMessage key={`m-${i}`} entry={entry} />;
          })}
          <div ref={transcriptEndRef} />

          {status === "error" && (
            <div className="mx-4 my-6 rounded-xl border border-[#ec403d]/30 bg-[#fff0ed] p-4 text-sm text-[#a43b38]">
              <strong>会议已停止：</strong>
              {errorMsg}
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={async () => {
                    // 把原议题写回 sessionStorage,跳回 setup 页让用户复盘重开
                    try {
                      const { saveCouncilDraft } = await import("@/lib/council-draft");
                      saveCouncilDraft({
                        topic,
                        topicCategory: null,
                        userBackground: "",
                      });
                    } catch {
                      // sessionStorage 不可用,直接跳也行
                    }
                    router.push("/council/setup");
                  }}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-[#30103f] to-[#4b1c62] px-4 py-2 text-xs font-bold text-white shadow-md transition hover:-translate-y-0.5"
                >
                  ↻ 再次召集会议
                </button>
              </div>
            </div>
          )}
        </div>
        {hasNewMessages && (
          <button
            type="button"
            onClick={jumpToLatest}
            className="absolute bottom-4 left-1/2 z-10 -translate-x-1/2 rounded-full bg-[#4b1c62] px-4 py-2 text-xs font-bold text-white shadow-lg transition hover:bg-[#623078]"
          >
            有新消息 ↓
          </button>
        )}
        </div>

        <ChatInput
          onSubmit={handleUserSubmit}
          disabled={status === "error" || status === "awaiting_input"}
          placeholder="补充背景、追问某位专家，或调整讨论方向…"
        />
      </section>

      <ExpertSeatPanel
        expertIds={selectedExpertIds}
        statuses={memberStatus}
        activeMember={activeMember}
        onSelect={(id) => setActiveMember(id)}
      />

      {/* 右侧议员详情抽屉 */}
      {activeMemberData?.member && (
        <MemberDetailDrawer
          name={activeMemberData.member.display_name}
          color={activeMemberData.member.color}
          role={activeMemberData.member.role}
          avatarChar={activeMemberData.member.avatar_char}
          model={activeMemberData.member.model}
          signatureLine={activeMemberData.member.signature_line}
          status={activeMemberData.status}
          entry={activeMemberData.latestEntry}
          toolCalls={activeMemberData.toolCalls}
          traces={activeMemberData.traces}
          onClose={() => setActiveMember(null)}
        />
      )}

      {status === "awaiting_input" && clarificationQuestions.length > 0 && (
        <ClarificationModal
          questions={clarificationQuestions}
          onSubmit={handleClarificationSubmit}
          onSkip={handleClarificationSkip}
        />
      )}

    </main>
  );
}

/**
 * 议员详情侧栏。
 */
function MemberDetailDrawer({
  name,
  color,
  role,
  avatarChar,
  model,
  signatureLine,
  status,
  entry,
  toolCalls,
  traces,
  onClose,
}: {
  name: string;
  color: string;
  role: string;
  avatarChar: string;
  model: string;
  signatureLine: string;
  status: MemberStatus;
  entry?: TranscriptEntry;
  toolCalls: any[];
  traces: AgentTraceEvent[];
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-30 flex justify-end"
      onClick={onClose}
    >
      <div className="absolute inset-0 bg-parliament-deep/30" />
      <div
        className="relative w-full max-w-sm bg-bg-surface h-full overflow-y-auto shadow-xl animate-slide-in"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶栏 */}
        <div className="p-5 border-b border-edge flex items-center justify-between bg-parliament text-white">
          <div className="flex items-center gap-3">
            <div
              className="w-10 h-10 rounded flex items-center justify-center text-white text-sm font-medium"
              style={{ backgroundColor: color }}
            >
              {avatarChar}
            </div>
            <div>
              <div className="font-medium">
                {name}
                <span className="ml-2 text-xs text-white/60">
                  {role}
                </span>
              </div>
              <div className="text-xs text-white/70">
                {
                  {
                    idle: "待命中",
                    thinking: "正在形成独立判断...",
                    using_tool: "正在核实关键事实...",
                    organizing: "正在整理分析依据...",
                    speaking: "正在发言...",
                    done: "已完成本轮",
                    degraded: "基于有限信息完成",
                    error: "出错",
                  }[status]
                }
              </div>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-white/80 hover:text-white text-xl leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4 text-sm">
          {/* V1-S4: 思维模型 */}
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-3 mb-2">
              思维模型
            </div>
            <div className="bg-bg-elev rounded-md p-3 text-ink">
              {model}
            </div>
          </div>

          {/* V1-S4: 标志句式 */}
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-3 mb-2">
              招牌问句
            </div>
            <div className="bg-bg-elev rounded-md p-3 text-ink-2 italic leading-relaxed">
              &ldquo;{signatureLine}&rdquo;
            </div>
          </div>

          {/* 最近发言 */}
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-3 mb-2">
              最近发言
            </div>
            {entry ? (
              <div className="bg-bg-elev rounded-md p-3 text-ink-2 leading-relaxed whitespace-pre-wrap">
                {entry.content || "（无内容）"}
              </div>
            ) : (
              <div className="text-ink-4 italic">暂未发言</div>
            )}
          </div>

          {/* 证据 */}
          {entry?.evidence && entry.evidence.length > 0 && (
            <div>
              <div className="text-xs uppercase tracking-wider text-ink-3 mb-2">
                证据（{entry.evidence.length}）
              </div>
              <ul className="space-y-2">
                {entry.evidence.map((e, i) => (
                  <li
                    key={i}
                    className="text-xs text-ink-2 bg-bg-elev rounded-md p-2.5"
                  >
                    <span className="text-ink-3 mr-1">[{e.strength}]</span>
                    {e.claim}
                    {e.source && (
                      <div className="text-ink-3 mt-1">— {e.source}</div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* V1-S4: 工具调用轨迹（真实渲染） */}
          <div>
            <div className="text-xs uppercase tracking-wider text-ink-3 mb-2 flex items-baseline justify-between">
              <span>自主调研路径</span>
              <span className="text-ink-4 normal-case tracking-normal text-[10px]">
                {traces.length} 个阶段
              </span>
            </div>
            {traces.length === 0 ? (
              <div className="text-ink-4 italic text-xs">暂无调研轨迹</div>
            ) : (
              <ul className="space-y-1.5">
                {traces.slice(-12).map((trace, i) => (
                  <li
                    key={i}
                    className="text-xs bg-bg-elev rounded-md p-2.5"
                  >
                    <div className="flex items-baseline justify-between mb-0.5">
                      <span className="font-medium text-ink">
                        {trace.public_message}
                      </span>
                      {trace.duration_ms !== null && trace.duration_ms !== undefined && (
                        <span className="text-ink-4 text-[10px]">
                          {(trace.duration_ms / 1000).toFixed(1)}s
                        </span>
                      )}
                    </div>
                    {trace.display_query && (
                      <div className="mt-1 break-words text-ink-3">
                        搜索：{trace.display_query}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {toolCalls.length > 0 && (
              <div className="mt-2 text-[10px] text-ink-4">
                已执行 {toolCalls.filter((item) => item.action === "tool_call").length} 次工具行动
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CoreDisputePanel({
  disputes,
}: {
  disputes: NonNullable<ParliamentSummary["core_disputes"]>;
}) {
  return (
    <section className="mx-4 mb-4 rounded-2xl border border-[#dfd3d8] bg-[#fffdfb] p-4 shadow-[0_10px_28px_rgba(62,36,57,0.05)]">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <p className="text-[10px] font-extrabold uppercase tracking-[0.14em] text-[#a36b6a]">
            Core Disputes
          </p>
          <h2 className="mt-0.5 text-sm font-bold text-[#3a323a]">
            本场正在解决的核心争议
          </h2>
        </div>
        <span className="rounded-full bg-[#f1ebee] px-2.5 py-1 text-[10px] font-bold text-[#736871]">
          {disputes.length} 项
        </span>
      </div>
      <ol className="space-y-2">
        {disputes.map((dispute, index) => (
          <li
            key={dispute.dispute_id}
            className="flex gap-2.5 rounded-xl bg-[#f7f3f5] px-3 py-2.5 text-xs leading-5 text-[#5f5660]"
          >
            <span className="grid h-5 w-5 flex-none place-items-center rounded-full bg-white font-bold text-[#6f4878]">
              {index + 1}
            </span>
            <span className="flex-1">{dispute.question}</span>
            {dispute.pressure_test && (
              <span className="self-start whitespace-nowrap rounded bg-[#fff0ed] px-1.5 py-0.5 text-[10px] font-bold text-[#a35a56]">
                共识压力测试
              </span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
