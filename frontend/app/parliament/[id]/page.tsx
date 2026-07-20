"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MemberStatusBar } from "@/components/MemberStatusBar";
import { ChatMessage } from "@/components/ChatMessage";
import { UserBubble } from "@/components/UserBubble";
import { SystemMessage } from "@/components/SystemMessage";
import { ChatInput } from "@/components/ChatInput";
import { subscribeParliament } from "@/lib/sse";
import {
  MemberId,
  MemberStatus,
  ParliamentSummary,
  TranscriptEntry,
  getMember,
} from "@/lib/types";
import {
  MEMBER_ENTRY_ORDER,
  MEMBER_OPENING_LINES,
  MEMBER_RESEARCH_DONE_PREFIX,
} from "@/lib/opening_lines";

const INITIAL_STATUS: Record<MemberId, MemberStatus> = {
  suan: "idle",
  chen: "idle",
  ji:   "idle",
  nian: "idle",
  fan:  "idle",
};

// 开场白间隔（毫秒）
const OPENING_INTERVAL_MS = 1200;

// V1-S1.3：每位议员开场白最长保留时间（90s）。
// 超时仍未替换为真实 research → 标记调研失败，移除开场白，status 设 error。
const OPENING_TIMEOUT_MS = 90_000;

export default function ParliamentPage() {
  const params = useParams();
  const router = useRouter();
  const parliamentId = params.id as string;

  const [topic, setTopic] = useState("");
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [toolCalls, setToolCalls] = useState<any[]>([]);
  const [memberStatus, setMemberStatus] = useState(INITIAL_STATUS);
  const [currentRound, setCurrentRound] = useState(0);
  const [status, setStatus] = useState<
    "connecting" | "running" | "done" | "error"
  >("connecting");
  const [summary, setSummary] = useState<ParliamentSummary | null>(null);
  const [duration, setDuration] = useState<number | undefined>(undefined);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // 用户选中的议员（右侧详情抽屉）
  const [activeMember, setActiveMember] = useState<MemberId | null>(null);

  // 用户的插话（MVP: 本地追加，不写后端）
  const [userMessages, setUserMessages] = useState<TranscriptEntry[]>([]);

  // "开场白"虚拟消息（每个议员一条"开始调研"）
  // 后端真实研究结果推回来时把这个 entry 替换为真实结论。
  const [openingEntries, setOpeningEntries] = useState<
    Partial<Record<MemberId, TranscriptEntry>>
  >({});

  // 是否已经触发过开场编排（防止 StrictMode 重跑两次）
  const openingStartedRef = useRef(false);

  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  // 合并 transcript：
  //   开场白（"开始调研"）—— 在议员**还没拿到研究结果**时显示
  //   服务端 research entry —— 拿到时展示完整结论，替换开场白
  //   debate / election / 用户消息 —— 按时间顺序追加
  const mergedTranscript = useMemo(() => {
    const all: TranscriptEntry[] = [];

    // 1) 找出后端 transcript 里每位议员的 research 结果
    const serverResearchByMember = new Map<MemberId, TranscriptEntry>();
    for (const e of transcript) {
      if (e.type === "research" && e.speaker !== "system" && e.speaker !== "user") {
        serverResearchByMember.set(e.speaker as MemberId, e);
      }
    }

    // 2) 议员按出场顺序：有研究结果就用研究结果，没有就放开场白
    for (const mid of MEMBER_ENTRY_ORDER) {
      const real = serverResearchByMember.get(mid);
      const opening = openingEntries[mid];
      if (!real && opening) {
        all.push(opening);
      } else if (real) {
        all.push(real);
      }
    }

    // 3) 后端其他类型条目（debate / election 等）按顺序追加
    //    跳过已在上面处理过的 research（避免重复）
    for (const e of transcript) {
      if (e.type === "research") continue;
      all.push(e);
    }

    // 4) 用户插话
    all.push(...userMessages);

    return all;
  }, [openingEntries, transcript, userMessages]);

  // 自动滚到底
  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mergedTranscript.length]);

  // === 开场编排：进入页面立刻让 5 议员依次发"开始调研" ===
  useEffect(() => {
    if (openingStartedRef.current || !parliamentId) return;
    openingStartedRef.current = true;

    let i = 0;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const tick = () => {
      if (i >= MEMBER_ENTRY_ORDER.length) return;
      const mid = MEMBER_ENTRY_ORDER[i];
      const member = getMember(mid);
      if (!member) {
        i++;
        timers.push(setTimeout(tick, OPENING_INTERVAL_MS));
        return;
      }
      // 注入开场白
      setOpeningEntries((prev) => ({
        ...prev,
        [mid]: {
          speaker: mid,
          type: "research",
          round: 0,
          content: MEMBER_OPENING_LINES[mid],
          timestamp: "刚刚",
        },
      }));
      // 状态 → 调研中
      setMemberStatus((prev) => ({
        ...prev,
        [mid]: "thinking",
      }));

      // V1-S1.3：90s 后若该议员仍是 thinking + 开场白还在 → 调研失败
      timers.push(
        setTimeout(() => {
          // 读取实时状态判断（用 functionalsetState 是不行的，需要借助外部）
          // 简化：直接清开场白 + 标记 error。mergeTranscript 会自然不显示。
          setOpeningEntries((prev) => {
            if (!prev[mid]) return prev; // 已经被后端替换了
            return { ...prev, [mid]: undefined };
          });
          setMemberStatus((prev) => {
            if (prev[mid] === "done" || prev[mid] === "error") return prev;
            return { ...prev, [mid]: "error" };
          });
        }, OPENING_TIMEOUT_MS)
      );

      i++;
      if (i < MEMBER_ENTRY_ORDER.length) {
        timers.push(setTimeout(tick, OPENING_INTERVAL_MS));
      }
    };
    timers.push(setTimeout(tick, 600)); // 等 0.6s 系统消息出来再开始
    return () => timers.forEach(clearTimeout);
  }, [parliamentId]);

  // === SSE 订阅 ===
  useEffect(() => {
    if (!parliamentId) return;

    const unsubscribe = subscribeParliament(
      parliamentId,
      (event) => {
        if (event.type === "parliament_started") {
          setStatus("running");
          if (event.payload?.topic) setTopic(event.payload.topic);
          return;
        }

        if (event.type === "node_complete") {
          const payload = event.payload || {};
          const node = event.node;

          // parse_topic / research 阶段：议员陆续 thinking
          if (node === "parse_topic" || node === "research") {
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
                next[k] = "done";
              });
              return next;
            });
            // 替换开场白：research 阶段 each member 完成后会被算到 transcript 里
            // 我们让 mergedTranscript 直接显示后端的研究结果，旧开场白再放到下面找不到
            // 简化：把开场白"清掉"（设为空），让真实 research 出现
            // 但 mergedTranscript 上面有跳过 logic，应该可以工作
          }

          if (payload.transcript) {
            setTranscript(payload.transcript as TranscriptEntry[]);
          }
          // V1-S4 接收工具调用轨迹
          if (payload.tool_calls) {
            setToolCalls(payload.tool_calls as any[]);
          }

          if (payload.current_round !== undefined) {
            const prevRound = currentRound;
            setCurrentRound(payload.current_round);

            // 阶段切换：从调研(R0)进入 R1 → 系统消息
            if (node === "debate" && payload.current_round > prevRound) {
              setMemberStatus({
                suan: "thinking", chen: "thinking",
                ji: "thinking", nian: "thinking", fan: "thinking",
              });
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
      (err) => {
        setStatus("error");
        setErrorMsg(err.message);
      }
    );
    return unsubscribe;
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

  const activeMemberData = activeMember
    ? {
        member: getMember(activeMember),
        status: memberStatus[activeMember],
        latestEntry: [...transcript]
          .reverse()
          .find((e) => e.speaker === activeMember),
        toolCalls: toolCalls.filter((c) => c.member === activeMember),
      }
    : null;

  const doneCount = useMemo(
    () => Object.values(memberStatus).filter((s) => s === "done").length,
    [memberStatus]
  );

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
    <main className="min-h-screen flex flex-col">
      {/* 深苔绿顶栏（压舱色） */}
      <header className="bg-parliament text-white">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between">
          <button
            onClick={() => router.push("/")}
            className="text-sm text-white/70 hover:text-white transition flex items-center gap-1"
          >
            ← 返回
          </button>
          <div className="text-sm font-medium truncate max-w-[50%] text-center">
            邪杠 · {topic || "议会中"}
          </div>
          {/* V1-S4: 取消按钮（议会进行中可见） */}
          {status === "connecting" || status === "running" ? (
            <button
              onClick={handleCancel}
              className="text-xs px-3 py-1 rounded bg-stance-againstText/80 hover:bg-stance-againstText text-white transition"
              title="结束议会，下个节点完成后停止"
            >
              ⏹ 结束
            </button>
          ) : (
            <div className="text-xs text-white/70">
              {status === "error" ? "出错" : "完成"}
            </div>
          )}
        </div>
        <div className="h-px gold-divider" />
      </header>

      {/* 议员状态条 */}
      <MemberStatusBar
        memberStatus={memberStatus}
        currentRound={currentRound}
        maxRound={3}
        doneCount={doneCount}
        activeMember={activeMember}
        onMemberClick={(id) =>
          setActiveMember((cur) => (cur === id ? null : id))
        }
      />

      {/* 群聊主区 */}
      <div className="flex-1 overflow-hidden bg-bg-chat">
        <div className="max-w-3xl mx-auto py-4">
          {/* 系统消息：你已加入会议室 */}
          <SystemMessage
            text={`你已加入「${topic || "议会"}」会议室`}
          />

          {/* connecting */}
          {status === "connecting" && (
            <div className="text-center py-8 text-ink-3">
              <div className="inline-block w-5 h-5 border-2 border-edge border-t-brand rounded-full animate-spin mb-2" />
              <div className="text-xs">正在召唤议员...</div>
            </div>
          )}

          {/* 消息列表 */}
          {mergedTranscript.length > 0 && (
            <div className="space-y-1">
              {mergedTranscript.map((entry, i) => {
                if (entry.speaker === "user") {
                  return <UserBubble key={`u-${i}`} entry={entry} />;
                }
                if (
                  entry.speaker === "system" ||
                  entry.type === "election"
                ) {
                  return (
                    <SystemMessage key={`s-${i}`} text={entry.content} />
                  );
                }
                return <ChatMessage key={`m-${i}`} entry={entry} />;
              })}
              <div ref={transcriptEndRef} />
            </div>
          )}

          {/* 错误 */}
          {status === "error" && (
            <div className="mx-4 my-6 p-4 bg-stance-againstBg border border-stance-againstText/30 rounded-md text-stance-againstText text-sm">
              <strong>议会出错：</strong>
              {errorMsg}
              <button
                onClick={() => router.push("/")}
                className="ml-3 underline hover:opacity-70"
              >
                返回首页
              </button>
            </div>
          )}
        </div>
      </div>

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
          onClose={() => setActiveMember(null)}
        />
      )}

      {/* 底部插话输入 */}
      <ChatInput
        onSubmit={handleUserSubmit}
        disabled={status === "error"}
      />
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
                  {role}派
                </span>
              </div>
              <div className="text-xs text-white/70">
                {
                  {
                    idle: "待命中",
                    thinking: "正在调研...",
                    speaking: "正在发言...",
                    done: "已完成本轮",
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
              <span>工具调用</span>
              <span className="text-ink-4 normal-case tracking-normal text-[10px]">
                {toolCalls.length} 次
              </span>
            </div>
            {toolCalls.length === 0 ? (
              <div className="text-ink-4 italic text-xs">暂无工具调用</div>
            ) : (
              <ul className="space-y-1.5">
                {toolCalls.slice(-10).reverse().map((c, i) => (
                  <li
                    key={i}
                    className="text-xs bg-bg-elev rounded-md p-2.5"
                  >
                    <div className="flex items-baseline justify-between mb-0.5">
                      <span className="font-medium text-ink">
                        {c.action || c.tool || "tool"}
                      </span>
                      {c.round !== undefined && (
                        <span className="text-ink-4 text-[10px]">
                          R{c.round}
                        </span>
                      )}
                    </div>
                    {c.error ? (
                      <div className="text-stance-againstText">
                        错误：{c.error}
                      </div>
                    ) : (
                      c.stance && (
                        <div className="text-ink-2">
                          立场 <span className="font-medium">{c.stance}</span>
                          {c.confidence !== undefined && (
                            <span className="text-ink-3 ml-1">
                              置信 {(c.confidence * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                      )
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
