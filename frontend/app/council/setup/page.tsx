"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ExpertSelectionCard } from "@/components/council/ExpertSelectionCard";
import {
  createParliament,
  previewExpertSelection,
  RateLimitError,
} from "@/lib/api";
import { CouncilDraft, loadCouncilDraft } from "@/lib/council-draft";
import { ExpertId, MEMBERS } from "@/lib/types";

const FALLBACK_EXPERTS: ExpertId[] = [
  "mathematician",
  "economist",
  "psychologist",
  "artist",
  "politician",
];

export default function CouncilSetupPage() {
  const router = useRouter();
  const [draft, setDraft] = useState<CouncilDraft | null>(null);
  const [selected, setSelected] = useState<ExpertId[]>([]);
  const [recommended, setRecommended] = useState<ExpertId[]>([]);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [coverage, setCoverage] = useState<string[]>([]);
  const [loadingRecommendation, setLoadingRecommendation] = useState(true);
  const [creating, setCreating] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const value = loadCouncilDraft();
    if (!value) {
      setLoadingRecommendation(false);
      return;
    }
    setDraft(value);
    let active = true;
    previewExpertSelection(
      value.topic,
      value.topicCategory || undefined,
      value.userBackground || undefined
    )
      .then((result) => {
        if (!active) return;
        const ids = result.recommended_expert_ids as ExpertId[];
        setRecommended(ids);
        setSelected(ids);
        setReasons(result.reasons);
        setCoverage(result.coverage);
      })
      .catch(() => {
        if (!active) return;
        setRecommended(FALLBACK_EXPERTS);
        setSelected(FALLBACK_EXPERTS);
        setNotice("推荐服务暂时不可用，已为你选用一组均衡视角，可继续调整。");
      })
      .finally(() => {
        if (active) setLoadingRecommendation(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const selectionSummary = useMemo(() => {
    if (selected.length < 3) return "至少选择 3 位讨论者";
    if (selected.length === 5) return "已选择 5 位";
    return `已选择 ${selected.length} 位，还可以选择 ${5 - selected.length} 位`;
  }, [selected.length]);

  const toggleExpert = (expertId: ExpertId) => {
    setNotice(null);
    setError(null);
    if (selected.includes(expertId)) {
      setSelected((current) => current.filter((id) => id !== expertId));
      return;
    }
    if (selected.length >= 5) {
      setNotice("本次最多选择 5 位，请先取消一位再加入新的讨论者。");
      return;
    }
    setSelected((current) => [...current, expertId]);
  };

  const startCouncil = async () => {
    if (!draft || selected.length < 3 || creating) return;
    setCreating(true);
    setError(null);
    try {
      const result = await createParliament(
        draft.topic,
        draft.topicCategory || undefined,
        draft.userBackground || undefined,
        undefined,
        selected
      );
      router.push(`/parliament/${result.parliament_id}`);
    } catch (caught) {
      setCreating(false);
      if (caught instanceof RateLimitError) {
        setError(caught.message);
      } else {
        setError(caught instanceof Error ? caught.message : "创建会议失败，请重试。");
      }
    }
  };

  if (!draft && !loadingRecommendation) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#fffaf6] px-6 text-[#251536]">
        <div className="max-w-sm text-center">
          <img
            src="/brand/xiegang-logo.webp"
            alt="斜杠"
            className="mx-auto w-36 mix-blend-multiply"
            loading="lazy"
          />
          <h1 className="mt-6 font-serif text-2xl">没有找到待讨论的问题</h1>
          <p className="mt-3 text-sm leading-6 text-[#746d78]">
            返回首页填写一个决策问题，再选择参与讨论的专家。
          </p>
          <button
            type="button"
            onClick={() => router.replace("/")}
            className="mt-6 min-h-11 rounded-[10px] bg-[#4b1c62] px-5 font-bold text-white"
          >
            返回首页
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#fffaf6] text-[#251536]">
      <header className="grid h-16 grid-cols-[1fr_auto_1fr] items-center border-b border-[#e9ded8] bg-[#fffdfb]/90 px-5 sm:h-[78px] sm:px-8 lg:px-16">
        <button
          type="button"
          onClick={() => router.push("/")}
          className="justify-self-start text-sm font-bold text-[#4b1c62] transition hover:text-[#ec403d]"
        >
          ← <span className="hidden sm:inline">返回修改问题</span>
        </button>
        <img
          src="/brand/xiegang-logo.webp"
          alt="斜杠"
          className="h-10 w-[88px] object-cover object-center mix-blend-multiply sm:h-12 sm:w-[106px]"
          loading="lazy"
        />
        <span className="justify-self-end text-xs font-bold tracking-[0.08em] text-[#978994]">
          <span className="hidden sm:inline">02 / </span>选择讨论者
        </span>
      </header>

      <div className="mx-auto grid min-h-[calc(100vh-78px)] max-w-[1220px] lg:grid-cols-[minmax(230px,.72fr)_minmax(0,2fr)]">
        <aside className="border-b border-[#e9ded8] bg-[#f6ebe4]/40 px-6 py-7 lg:border-b-0 lg:border-r lg:px-12 lg:py-16">
          <p className="text-xs font-extrabold tracking-[0.13em] text-[#ec403d]">
            本次议题
          </p>
          {draft ? (
            <>
              <h1 className="mt-3 font-serif text-[22px] leading-relaxed sm:text-[30px]">
                {draft.topic}
              </h1>
              {draft.userBackground && (
                <div className="mt-5 rounded-xl border border-[#e9ded8] bg-white/55 p-4">
                  <p className="text-xs font-bold text-[#4b3e4d]">关键背景</p>
                  <p className="mt-2 text-[13px] leading-6 text-[#746d78]">
                    {draft.userBackground}
                  </p>
                </div>
              )}
              <p className="mt-5 text-[13px] leading-6 text-[#746d78]">
                系统已根据你的问题推荐 5 位专家。你可以调整，本次讨论需保留
                3–5 位。
              </p>
              {coverage.length > 0 && (
                <div className="mt-5">
                  <p className="text-xs font-bold text-[#4b3e4d]">建议覆盖</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {coverage.map((item) => (
                      <span
                        key={item}
                        className="rounded-full border border-[#e9ded8] bg-white/70 px-2.5 py-1 text-[11px] text-[#746d78]"
                      >
                        {item}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="mt-4 h-28 animate-pulse rounded-xl bg-white/60" />
          )}
        </aside>

        <section className="relative px-5 pb-32 pt-8 sm:px-8 lg:px-[70px] lg:pt-14">
          <div className="mb-6 flex items-end justify-between gap-5">
            <div>
              <p className="text-xs font-extrabold tracking-[0.13em] text-[#ec403d]">
                六种思考方法
              </p>
              <h2 className="mt-2 font-serif text-[27px] leading-tight sm:text-[38px]">
                让哪些视角参与讨论？
              </h2>
            </div>
            <span className="hidden text-xs text-[#978994] sm:block">
              {loadingRecommendation ? "正在推荐…" : "默认已选 5 位"}
            </span>
          </div>

          <div className="grid gap-3.5 sm:grid-cols-2">
            {MEMBERS.map((expert) => (
              <ExpertSelectionCard
                key={expert.id}
                expert={expert}
                reason={
                  reasons[expert.id] ||
                  expert.signature_line ||
                  expert.model
                }
                selected={selected.includes(expert.id as ExpertId)}
                recommended={recommended.includes(expert.id as ExpertId)}
                onToggle={() => toggleExpert(expert.id as ExpertId)}
              />
            ))}
          </div>

          {(notice || error) && (
            <div
              className={`mt-4 rounded-xl border px-4 py-3 text-sm ${
                error
                  ? "border-[#ec403d]/30 bg-[#fff0ed] text-[#a43b38]"
                  : "border-[#e9ded8] bg-white/70 text-[#746d78]"
              }`}
              role="status"
            >
              {error || notice}
            </div>
          )}

          <div className="fixed bottom-3 left-4 right-4 z-20 flex items-center justify-between gap-4 rounded-2xl border border-[#e9ded8] bg-[#fffdfb]/95 px-4 py-3 shadow-[0_14px_42px_rgba(54,28,53,0.14)] backdrop-blur sm:left-auto sm:right-8 sm:w-auto sm:px-5 lg:right-[max(24px,calc((100vw-1220px)/2))]">
            <span className="text-[13px] text-[#746d78]">{selectionSummary}</span>
            <button
              type="button"
              onClick={startCouncil}
              disabled={selected.length < 3 || loadingRecommendation || creating}
              className="inline-flex min-h-11 items-center justify-center gap-3 rounded-[10px] bg-gradient-to-br from-[#30103f] to-[#4b1c62] px-5 font-bold text-white shadow-[0_9px_18px_rgba(60,20,78,0.18)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none disabled:hover:translate-y-0"
            >
              {creating ? "正在创建…" : "开始讨论 →"}
            </button>
          </div>
        </section>
      </div>
    </main>
  );
}
