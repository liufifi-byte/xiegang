"""辩论节点：固定 3 轮，每轮 5 议员并行发言。

辩论阶段议员之间可见，需要互相质询/反驳/补充。

关键规则：
- 每轮 5 议员能在看完整轮发言后并行准备
- 议员可以选择：质询某人 / 反驳某人 / 补充自己 / 引入新证据 / 弃权
- 张力（离散度 + 质询活跃度）写入 state 给 UI 显示用

V1-S2: asyncio.gather 并发 5 议员
V1-S3: 每轮开始前从 runtime_context 拉用户插话 → 注入议员 prompt
"""
import asyncio
import time

from app.llm import get_structured_llm
from app.llm_concurrency import get_llm_semaphore
from app.runtime_context import consume_user_interrupt
from app.schemas.member import MemberStatement


# 议员 mapping：id → (display_name, role_label)
MEMBER_INFO: dict[str, tuple[str, str]] = {
    "suan": ("老算", "理性"),
    "chen": ("老沉", "保守"),
    "ji":   ("小机", "机会"),
    "nian": ("十年", "长期"),
    "fan":  ("反对", "反共识"),
}


DEBATE_PROMPT = """你是{member_name}，邪杠议会{member_role}派议员。

## 当前议会状态
【议题】{topic}
【关键变量】{variables}

## 前序发言记录
{history}

## 你的工作
基于其他议员的发言，发表你第 {round_num} 轮的发言。你**必须**做以下至少一件事：
- 质询某议员（必须引用他的具体原话）
- 反驳某议员（必须指出他的逻辑漏洞）
- 补充自己的立场（必须有新证据，不能重复之前说的）
- 弃权（仅在完全同意他人观点时）

## 输出要求
严格 JSON（不要 markdown 代码块、不要其他文字）：
{{
  "member_id": "{member_id}",
  "stance": "for|against|neutral",
  "confidence": 0.0-1.0,
  "evidence": [{{"claim": "...", "source": "...", "strength": "high|medium|low"}}],
  "reasoning_summary": "本轮发言正文（需明确引用对方原话）"
}}"""


INTERRUPT_PROMPT_BLOCK = """

## 用户在本轮发言前补充
用户在群里说："{user_interrupt}"
请注意：你必须在发言中**显式回应用户这句话**——支持 / 反对 / 补充 / 引入新证据都行。
"""


def _format_history(transcript: list[dict], member_id: str, round_num: int) -> str:
    """把已经发生的发言格式化给一个议员看。"""
    if not transcript:
        return "（这是第一轮发言，暂无历史）"

    lines = []
    for entry in transcript:
        speaker = entry.get("speaker", "?")
        r = entry.get("round", 0)
        content = entry.get("content", "")
        stance = entry.get("stance", "")
        if len(content) > 300:
            content = content[:300] + "..."
        lines.append(f"[{speaker} R{r} | {stance}]: {content}")
    return "\n".join(lines)


async def _debate_one_member_async(
    mid: str,
    name: str,
    role: str,
    topic: str,
    variables: list[str],
    next_round: int,
    history_str: str,
    user_interrupt: str | None = None,
    parliament_id: str | None = None,
) -> MemberStatement:
    """单个议员本轮发言（异步版本）。V2-S2 切真 ainvoke + cancel_guard。"""
    prompt = DEBATE_PROMPT.format(
        member_name=name,
        member_role=role,
        member_id=mid,
        topic=topic,
        variables=", ".join(variables) if variables else "（无）",
        round_num=next_round,
        history=history_str,
    )

    # V1-S3: 把用户插话塞进 prompt
    if user_interrupt:
        prompt += INTERRUPT_PROMPT_BLOCK.format(user_interrupt=user_interrupt)

    structured = get_structured_llm(MemberStatement, temperature=0.7)
    sem = get_llm_semaphore()
    async with sem:
        # V2-S2: 真异步 + cancel_guard
        from app.cancel_guard import guard_ainvoke
        return await guard_ainvoke(
            parliament_id=parliament_id or "_no_parliament",
            coro=structured.ainvoke(prompt),
            timeout=60,  # P0-S1.2: 单 LLM 调用硬上限 60s
        )


async def _debate_member_safe(
    mid: str,
    name: str,
    role: str,
    topic: str,
    variables: list[str],
    next_round: int,
    history_str: str,
    user_interrupt: str | None = None,
    parliament_id: str | None = None,
) -> dict:
    """包装，单议员失败不影响别人。"""
    try:
        stmt = await _debate_one_member_async(
            mid, name, role, topic, variables, next_round,
            history_str, user_interrupt, parliament_id,
        )
        return {"ok": True, "data": stmt}
    except Exception as e:
        return {"ok": False, "error": e, "speaker": mid, "round": next_round}


async def run_debate_round(state: dict) -> dict:
    """跑 1 轮辩论（V1-S2 异步并发版 + V1-S3 用户插话注入）。"""
    topic = state["topic"]
    variables = state["briefing"].get("key_variables", [])
    next_round = state["current_round"] + 1

    full_history = state.get("transcript", [])
    history_str = _format_history(full_history, "", next_round)

    initial_stances = state.get("members", {})

    # V1-S3: 从 runtime_context 取用户插话（一次性消费）
    user_interrupt: str | None = None
    parliament_id = state.get("parliament_id")
    if parliament_id:
        user_interrupt = await consume_user_interrupt(parliament_id)

    # 如果有用户插话 → 写入 transcript 一条 user_interrupt
    # 这样 SSE 推送时前端能看到（虽然前端也乐观渲染了），
    # 关键是 archive 时这条会进数据库
    new_transcript = list(full_history)
    if user_interrupt:
        new_transcript.append({
            "speaker": "user",
            "type": "user_interrupt",
            "round": next_round,
            "content": user_interrupt,
            "timestamp": time.strftime("%H:%M", time.localtime()),
        })
        # 因为新的 history 已包含用户插话，重算 history_str
        history_str = _format_history(new_transcript, "", next_round)

    # 5 议员并发
    tasks = [
        _debate_member_safe(
            mid, name, role, topic, variables, next_round,
            history_str, user_interrupt, parliament_id,
        )
        for mid, (name, role) in MEMBER_INFO.items()
    ]
    results = await asyncio.gather(*tasks)

    new_entries: list[dict] = []
    tool_calls_log: list[dict] = []

    for outcome in results:
        if outcome["ok"]:
            stmt: MemberStatement = outcome["data"]
            entry = {
                "speaker": stmt.member_id,
                "round": next_round,
                "type": "debate",
                "stance": stmt.stance,
                "content": stmt.reasoning_summary,
                "evidence": [e.model_dump() for e in stmt.evidence],
                "confidence": stmt.confidence,
            }
            new_entries.append(entry)
            tool_calls_log.append({
                "member": stmt.member_id,
                "action": "debate_speak",
                "round": next_round,
                "timestamp": time.time(),
            })
        else:
            err = outcome["error"]
            speaker = outcome.get("speaker", "?")
            new_entries.append({
                "speaker": speaker,
                "round": next_round,
                "type": "debate_error",
                "stance": "neutral",
                "content": f"[DEBATE ERROR] {err}",
                "evidence": [],
                "confidence": 0.0,
            })
            tool_calls_log.append({
                "member": speaker,
                "action": "debate_error",
                "round": next_round,
                "error": str(err),
                "timestamp": time.time(),
            })

    # 张力
    stances = [e["stance"] for e in new_entries if e.get("stance")]
    unique_count = len(set(stances))
    tension = unique_count / 3.0

    new_members_state = dict(initial_stances)
    for entry in new_entries:
        new_members_state[entry["speaker"]] = entry

    return {
        "transcript": new_transcript + new_entries,
        "tool_calls": state.get("tool_calls", []) + tool_calls_log,
        "current_round": next_round,
        "tension_score": tension,
        "members": new_members_state,
    }
