"""议长选举节点：5 议员三槽位自荐 + 规则评分 + 兜底。

设计原则（参考交接文档）：
1. 强制反对票：每个议员必须推荐他人（不能选自己）
2. 三槽位强制：A 推荐+引用 / B 自证偏见 / C 综合方法
3. 规则评分（不用 LLM 评分，避免随机性）：
   - 槽位 A：引用对方原话长度 > 10 字 → +1
   - 槽位 B：包含"我"+"偏见/盲/弱/倾向"等关键词 → +1
   - 槽位 C：包含动词（"我会"等）且长度 > 15 字 → +1
4. 兜底机制：
   - 票太分散（最高票 < 总票数 30%）→ 反对 任议长
   - 任一评分 = 0 → 反对 任议长
   - 主调度公开声明兜底理由
"""
import time

from app.llm import get_structured_llm
from app.schemas.member import ElectionPitch
from app.agents.profiles import runtime_member_info


MEMBER_INFO: dict[str, tuple[str, str]] = {
    "suan": ("老算", "理性"),
    "chen": ("老沉", "保守"),
    "ji":   ("小机", "机会"),
    "nian": ("十年", "长期"),
    "fan":  ("反对", "反共识"),
}

# 兜底议长（设计文档 §3 决策 7）
FALLBACK_CHAIR = "fan"


PITCH_PROMPT = """你是{member_name}，本场专业身份是：{member_role}。

## 当前议会状态
议题：{topic}

## 其他议员的辩论立场摘要
{other_members}

## 你的任务：议长选举三槽位发言（每个槽位必填，缺一算弃权）

【槽位 A：推荐某人】你必须推荐**除自己之外**的某人。
1) 这个人的什么具体观点打动了你（必须引用原话 > 10 字）
2) 这个人的什么盲区需要别人补

【槽位 B：自证偏见】
你承认自己的偏见是什么？如何抵消？（必须明确说"我的偏见是..."）

【槽位 C：综合方法】
如果你做议长，你会怎么综合 5 个视角？（必须包含具体步骤，不能空话）

严格 JSON 输出（不要 markdown、不要其他文字）：
{{
  "member_id": "{member_id}",
  "recommend_target": "被推荐的议员 ID",
  "target_specific_view": "引用对方具体观点（原话）",
  "target_blind_spot": "对方的盲区",
  "own_bias": "自己的偏见 + 如何抵消",
  "synthesis_method": "综合方法（含具体步骤）"
}}"""


# ============ 评分规则（不用 LLM）============

# 槽位 B 偏见关键词
BIAS_KEYWORDS = ("偏见", "盲", "弱", "倾向", "偏向", "容易", "过度", "忽略", "忽视")
# 槽位 C 动词
SYNTH_VERBS = ("我会", "先", "列出", "排序", "汇总", "对照", "标记", "给出", "总结")


def _score_slot_a(target_specific_view: str) -> int:
    """槽位 A：引用对方原话长度 > 10 字 → +1"""
    return 1 if len(target_specific_view.strip()) > 10 else 0


def _score_slot_b(own_bias: str) -> int:
    """槽位 B：包含"偏见"类关键词 + 包含"我" → +1"""
    text = own_bias.strip()
    if len(text) < 5:
        return 0
    has_bias_word = any(kw in text for kw in BIAS_KEYWORDS)
    has_self = "我" in text
    return 1 if (has_bias_word and has_self) else 0


def _score_slot_c(synthesis_method: str) -> int:
    """槽位 C：长度 > 15 字 + 包含动作动词 → +1"""
    text = synthesis_method.strip()
    if len(text) < 15:
        return 0
    has_verb = any(v in text for v in SYNTH_VERBS)
    return 1 if has_verb else 0


def _score_pitch(pitch: ElectionPitch) -> int:
    """单段发言的 0-3 分（不打 LLM，纯规则）。"""
    return (
        _score_slot_a(pitch.target_specific_view)
        + _score_slot_b(pitch.own_bias)
        + _score_slot_c(pitch.synthesis_method)
    )


# ============ 节点主流程 ============

def _generate_pitches(state: dict) -> list[ElectionPitch]:
    """5 个议员各生成一段三槽位自荐发言。"""
    # 用其他议员的最新立场摘要
    other_lines = []
    for mid, data in state.get("members", {}).items():
        stance = data.get("stance", "?")
        confidence = data.get("confidence", 0)
        summary = (
            data.get("reasoning_summary")
            or data.get("content")
            or ""
        )
        other_lines.append(
            f"[{mid}] 立场={stance} 置信={confidence}: {summary[:100]}"
        )
    other_text = "\n".join(other_lines) if other_lines else "（无）"

    member_info = runtime_member_info(state, MEMBER_INFO)
    fallback_chair = _fallback_chair_for_state(state, member_info)
    pitches: list[ElectionPitch] = []
    for mid, (name, role) in member_info.items():
        prompt = PITCH_PROMPT.format(
            member_name=name,
            member_role=role,
            topic=state["topic"],
            other_members=other_text,
            member_id=mid,
        )
        try:
            structured = get_structured_llm(ElectionPitch, temperature=0.6)
            pitch: ElectionPitch = structured.invoke(prompt)
            pitch.member_id = mid
            # 强制约束：不能推荐自己
            if pitch.recommend_target == mid:
                pitch.recommend_target = next(
                    candidate for candidate in member_info if candidate != mid
                )
            pitches.append(pitch)
        except Exception:
            pitches.append(ElectionPitch(
                member_id=mid,
                recommend_target=(
                    fallback_chair
                    if fallback_chair != mid
                    else next(candidate for candidate in member_info if candidate != mid)
                ),
                target_specific_view="[ERROR]",
                target_blind_spot="[ERROR]",
                own_bias="[ERROR] 我的偏见是生成失败",
                synthesis_method="[ERROR]",
            ))
    return pitches


def _score_all_pitches(pitches: list[ElectionPitch]) -> dict[str, int]:
    """给每段发言打分（纯规则）。返回 {member_id: score}"""
    return {p.member_id: _score_pitch(p) for p in pitches}


def _fallback_chair_for_state(
    state: dict,
    member_info: dict[str, tuple[str, str]] | None = None,
) -> str:
    member_info = member_info or runtime_member_info(state, MEMBER_INFO)
    if not state.get("selected_expert_ids"):
        return FALLBACK_CHAIR
    # V3：兜底议长由逆向思考者担任（原 contrarian_challenge 职责位）
    contrarian = next(
        (mid for mid in member_info if mid == "contrarian"),
        None,
    )
    if contrarian in member_info:
        return contrarian
    return next(iter(member_info))


def run_election(state: dict) -> dict:
    """LangGraph 节点：议长选举。"""
    pitches = _generate_pitches(state)
    scores = _score_all_pitches(pitches)
    member_info = runtime_member_info(state, MEMBER_INFO)
    fallback_chair = _fallback_chair_for_state(state, member_info)

    # 计算每个议员的得票：被推荐次数 × 推荐者评分
    vote_tally: dict[str, float] = {}
    recommenders: dict[str, list[str]] = {}
    for p in pitches:
        target = p.recommend_target
        if target not in member_info:
            # 推荐了无效 ID，丢弃
            continue
        if target == p.member_id:
            # 安全网：不应发生（前面已修正）
            continue
        vote_tally[target] = vote_tally.get(target, 0.0) + scores.get(p.member_id, 0)
        recommenders.setdefault(target, []).append(p.member_id)

    total_votes = sum(vote_tally.values()) if vote_tally else 0
    max_votes = max(vote_tally.values()) if vote_tally else 0

    fallback_reason = None
    winner = (
        max(vote_tally, key=vote_tally.get) if vote_tally else None
    )

    # 兜底条件 1：票太分散
    if total_votes > 0 and (max_votes / total_votes) < 0.3:
        winner = fallback_chair
        fallback_reason = "票过于分散（最高票 < 总票数 30%）"

    # 兜底条件 2：所有发言评分都 == 0
    elif all(s == 0 for s in scores.values()):
        winner = fallback_chair
        fallback_reason = "所有议员发言评分均为 0（推荐理由质量不达标）"

    elif not winner:
        winner = fallback_chair
        fallback_reason = "无有效投票"

    fallback_used = fallback_reason is not None

    election_result = {
        "candidates": [
            {
                "member_id": p.member_id,
                "recommend_target": p.recommend_target,
                "target_specific_view": p.target_specific_view,
                "target_blind_spot": p.target_blind_spot,
                "own_bias": p.own_bias,
                "synthesis_method": p.synthesis_method,
                "score": scores.get(p.member_id, 0),
            }
            for p in pitches
        ],
        "vote_tally": vote_tally,
        "recommenders": recommenders,
        "winner": winner,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "timestamp": time.time(),
    }

    transcript_entry = {
        "type": "election",
        "speaker": "system",
        "content": (
            f"议长选举完成，当选：{winner}"
            + (f"（兜底：{fallback_reason}）" if fallback_used else "（正常）")
        ),
    }

    return {
        "election_result": election_result,
        "transcript": state.get("transcript", []) + [transcript_entry],
    }
