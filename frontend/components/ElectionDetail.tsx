"use client";

import { ElectionResult, getMember } from "@/lib/types";

interface Props {
  election: ElectionResult;
}

/**
 * 议长选举详情：5 议员互投记录 + 票选条形图 + 兜底声明。
 *
 * V1-S1.2 修复 #4：之前 SummaryPanel 只显示一行"议长选举完成"，
 * 看不到候选人 / 票数 / 得分 / 兜底原因。
 */
export function ElectionDetail({ election }: Props) {
  const maxVotes = Math.max(...Object.values(election.vote_tally || {}), 1);
  const sortedTally = Object.entries(election.vote_tally || {}).sort(
    ([, a], [, b]) => b - a
  );

  return (
    <div className="space-y-5">
      {/* 兜底提示（如果有） */}
      {election.fallback_used && election.fallback_reason && (
        <div className="p-3 rounded-md bg-stance-againstBg border border-stance-againstText/30 text-stance-againstText text-sm flex gap-2 items-start">
          <span className="text-base leading-none">⚠️</span>
          <div>
            <div className="font-medium mb-0.5">兜底任命</div>
            <div className="text-xs opacity-80">{election.fallback_reason}</div>
          </div>
        </div>
      )}

      {/* 票选汇总：5 个议员的得票条 */}
      {sortedTally.length > 0 && (
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-3 mb-2">
            票选汇总
          </div>
          <div className="space-y-1.5">
            {sortedTally.map(([mid, votes]) => {
              const m = getMember(mid);
              if (!m) return null;
              const pct = (votes / maxVotes) * 100;
              const isWinner = mid === election.winner;
              return (
                <div key={mid} className="flex items-center gap-2">
                  <span
                    className="w-12 text-xs font-medium flex-shrink-0"
                    style={{ color: m.color }}
                  >
                    {m.display_name}
                    {isWinner && <span className="ml-1">★</span>}
                  </span>
                  <div className="flex-1 h-5 bg-bg-elev rounded relative overflow-hidden">
                    <div
                      className="h-full rounded transition-all"
                      style={{
                        backgroundColor: m.color,
                        width: `${pct}%`,
                        opacity: isWinner ? 1 : 0.7,
                      }}
                    />
                    <span className="absolute inset-0 flex items-center px-2 text-xs text-white font-medium">
                      {votes} 票
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          {sortedTally.length === 0 && (
            <div className="text-xs text-ink-3 italic">无有效投票</div>
          )}
        </div>
      )}

      {/* 5 候选人详情卡片 */}
      <div>
        <div className="text-xs uppercase tracking-wider text-ink-3 mb-2">
          自荐发言（按议员顺序）
        </div>
        <div className="space-y-2.5">
          {election.candidates?.map((c) => {
            const recommender = getMember(c.member_id);
            const target = getMember(c.recommend_target);
            if (!recommender || !target) return null;
            return (
              <div
                key={c.member_id}
                className="border border-edge rounded-md p-3 bg-white"
              >
                {/* 推荐关系 */}
                <div className="flex items-center gap-1.5 mb-2">
                  <Avatar member={recommender} />
                  <span
                    className="text-xs font-medium"
                    style={{ color: recommender.color }}
                  >
                    {recommender.display_name}
                  </span>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" className="text-ink-3">
                    <path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  <Avatar member={target} />
                  <span
                    className="text-xs font-medium"
                    style={{ color: target.color }}
                  >
                    {target.display_name}
                  </span>
                  <span className="ml-auto text-xs text-ink-3">
                    评分 {c.score} / 3
                  </span>
                </div>

                {/* 槽位 A 引用原话 */}
                <SlotBox label="引用" body={c.target_specific_view} />

                {/* 槽位 B 自证偏见 */}
                <SlotBox label="自证偏见" body={c.own_bias} />

                {/* 槽位 C 综合方法 */}
                <SlotBox label="综合方法" body={c.synthesis_method} />

                {/* 评分条 */}
                <div className="mt-2 flex items-center gap-0.5">
                  {[0, 1, 2].map((i) => (
                    <div
                      key={i}
                      className={`flex-1 h-1 rounded ${
                        i < c.score ? "bg-brand" : "bg-edge"
                      }`}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Avatar({ member }: { member: NonNullable<ReturnType<typeof getMember>> }) {
  return (
    <div
      className="w-5 h-5 rounded flex items-center justify-center text-white text-[10px] font-medium"
      style={{ backgroundColor: member.color }}
    >
      {member.avatar_char}
    </div>
  );
}

function SlotBox({ label, body }: { label: string; body: string }) {
  if (!body || body.startsWith("[ERROR") || body.startsWith("[ERROR]")) return null;
  return (
    <div className="text-xs leading-relaxed mt-1.5 text-ink-2">
      <span className="text-ink-3 mr-1.5">{label}：</span>
      {body}
    </div>
  );
}
