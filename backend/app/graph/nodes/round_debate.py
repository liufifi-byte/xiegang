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
from app.llm_concurrency import acquire_llm_slots
from app.runtime_context import consume_user_interrupt
from app.schemas.member import MemberStatement
from app.agents.profiles import (
    DUTY_LABELS,
    get_expert_profile,
    runtime_member_info,
)


# 议员 mapping：id → (display_name, role_label)
MEMBER_INFO: dict[str, tuple[str, str]] = {
    "suan": ("老算", "理性"),
    "chen": ("老沉", "保守"),
    "ji":   ("小机", "机会"),
    "nian": ("十年", "长期"),
    "fan":  ("反对", "反共识"),
}


DEBATE_PROMPT = """你是 Decision Council 的{member_name}。

## 你的职业宪法
{member_role}

## 当前议会状态
【议题】{topic}
【关键变量】{variables}

## 前序发言记录
{history}

## 你的工作
发表你第 {round_num} 轮的发言。
{round_rules}

共同规则：
- 事实、计算和推断假设必须明确区分
- 没有可核验来源时不得生成精确市场数据或历史统计
- 可以调整立场，但必须说明是哪条新信息改变了判断
- 当对方的证据比你的更可靠、或指出你忽略的关键变量时，**必须**承认——这是专业判断的演化，不是人格缺陷。被说服的发言比硬挺到底更有价值。
- 不能重复前面已经说过的内容
- 模仿职业范例的锐度和判断密度，但绝不能复制范例中的数字、年份和事实；禁止戏剧化比喻与情感操控词
- 不要用“各有利弊、因人而异、建议综合考虑”代替专业判断

## 输出要求
严格 JSON（不要 markdown 代码块、不要其他文字）：
{{
  "member_id": "{member_id}",
  "stance": "for|against|neutral",
  "confidence": 0.0-1.0,
  "evidence": [{{"claim": "...", "source": "...", "strength": "high|medium|low"}}],
  "reasoning_summary": "符合本轮结构和字数要求的完整正式发言"
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
        # Independent judgments are intentionally long and structured. Preserve
        # enough of them for later experts to respond to the actual analysis.
        content_limit = 700 if r == 0 else 450
        if len(content) > content_limit:
            content = content[:content_limit] + "..."
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
    if next_round == 1:
        round_rules = """这是第一轮全员立场陈述，正文 250-400 字。使用清晰的小标题或自然分段，必须包含：
- 【我的判断】：当前倾向与核心理由
- 【关键依据】：最重要的一项分析依据，并标明属于用户信息、网页来源、计算结果还是推断假设
- 【主要风险】：最担心的一个风险
- 【我的质疑】：点名质疑另一位专家的一个具体判断
- 【成立条件】：当前结论在什么条件下成立"""
    elif next_round == 2:
        round_rules = """这是第二轮交叉质询，正文 180-300 字，只围绕一个核心争议展开。必须准确引用或复述另一位专家的具体前提，并完成至少一项：
- 反驳该前提
- 补充一项新证据或新推理
- 指出事实判断与价值排序的区别
- 提出一个可执行替代方案
结尾**必须**说明这次回应是否改变了你的立场，并给出两种范例模板之一：
  被说服时：「我前一轮过于强调 X，但 [议员名] 的 Y 证据让我调整立场至 [for/against/neutral]」
  仍坚持时：「[议员名] 的 Z 不能改变我的判断，因为它低估了 [我的核心证据]」
但真诚优先：确认被说服就调整，不要为了显得稳健而硬撑旧立场。"""
    else:
        round_rules = """这是第三轮收敛与压力测试，正文 150-250 字。不得重复前两轮，必须说明：
- 剩余分歧属于事实、推理还是价值选择
- 当前更成立的条件判断
- 结论翻转条件或用户下一步行动"""
    prompt = DEBATE_PROMPT.format(
        member_name=name,
        member_role=role,
        member_id=mid,
        topic=topic,
        variables=", ".join(variables) if variables else "（无）",
        round_num=next_round,
        round_rules=round_rules,
        history=history_str,
    )

    # V1-S3: 把用户插话塞进 prompt
    if user_interrupt:
        prompt += INTERRUPT_PROMPT_BLOCK.format(user_interrupt=user_interrupt)

    structured = get_structured_llm(MemberStatement, temperature=0.7)
    # 公平分槽：与 parallel_research lock-ordering 一致（先全局后议会）
    async with acquire_llm_slots(parliament_id):
        # V2-S2: 真异步 + cancel_guard
        from app.cancel_guard import guard_ainvoke
        return await guard_ainvoke(
            parliament_id=parliament_id or "_no_parliament",
            coro=structured.ainvoke(prompt),
            timeout=150,  # 单 LLM 调用硬上限（含 GLM 慢响应；HTTP 90s + 余量）
        )


def _professional_role_prompt(
    member_id: str,
    fallback_role: str,
    duty: str,
) -> str:
    """Build the complete professional constitution used in every debate round."""
    try:
        profile = get_expert_profile(member_id)
    except ValueError:
        return f"固定角色：{fallback_role}；本轮职责：{duty}。"
    return "\n".join(
        (
            f"专业身份：{profile.professional_identity}",
            f"专业方法：{'；'.join(profile.analysis_methods)}",
            f"必须检查：{'；'.join(profile.required_questions)}",
            f"表达风格：{'；'.join(profile.communication_style)}",
            f"标志性问题：{profile.signature_line}",
            (
                "专业边界："
                f"{'；'.join(profile.forbidden_inferences + profile.professional_boundaries)}"
            ),
            f"本轮额外职责：{duty}。职责只改变审查重点，不覆盖职业人格。",
            "腔调范例：\n" + "\n".join(profile.voice_examples),
        )
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
    """包装，单议员失败不影响别人。

    日志：在原有 logger.warning 基础上再补一份结构化 print，
    覆盖 OK / TIMEOUT / CANCELLED / ERROR 四类，方便后端控制台快速 grep。
    用于排查"该专家本轮调研未完成"这种脑补兜底文（往往因为前一阶段 transcript 里
    带了 [RESEARCH ERROR] 占位，模型看着没数据可辩就自行补一句）。
    """
    import asyncio as _asyncio
    import logging
    import time as _time
    import traceback

    logger = logging.getLogger(__name__)
    t0 = _time.time()
    try:
        stmt = await _debate_one_member_async(
            mid, name, role, topic, variables, next_round,
            history_str, user_interrupt, parliament_id,
        )
        dur = _time.time() - t0
        print(
            f"[debate] OK  parliament={parliament_id} member={mid} "
            f"round={next_round} dur={dur:.1f}s stance={stmt.stance} "
            f"conf={stmt.confidence:.2f}",
            flush=True,
        )
        return {"ok": True, "data": stmt}
    except _asyncio.TimeoutError as e:
        dur = _time.time() - t0
        print(
            f"[debate] TIMEOUT  parliament={parliament_id} member={mid} "
            f"round={next_round} dur={dur:.1f}s (>120s cancel_guard 砍断) err={e!r}",
            flush=True,
        )
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "speaker": mid, "round": next_round}
    except _asyncio.CancelledError as e:
        dur = _time.time() - t0
        print(
            f"[debate] CANCELLED  parliament={parliament_id} member={mid} "
            f"round={next_round} dur={dur:.1f}s err={e!r}",
            flush=True,
        )
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "speaker": mid, "round": next_round}
    except Exception as e:
        dur = _time.time() - t0
        tb = traceback.format_exc()
        logger.warning("议员 %s 第 %d 轮辩论失败:\n%s", mid, next_round, tb)
        print(
            f"[debate] ERROR  parliament={parliament_id} member={mid} "
            f"round={next_round} dur={dur:.1f}s type={type(e).__name__} err={e!r}",
            flush=True,
        )
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "speaker": mid, "round": next_round}


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

    member_info = runtime_member_info(state, MEMBER_INFO)
    debate_duties = state.get("debate_duties", {})

    # 本场 5 位议员并发。职业方法保持稳定，议会职责只约束本轮审查重点。
    tasks = [
        _debate_member_safe(
            mid,
            name,
            _professional_role_prompt(
                mid,
                role,
                DUTY_LABELS.get(
                    debate_duties.get(mid, ""),
                    "专业质询",
                ),
            )
            if state.get("selected_expert_ids")
            else role,
            topic,
            variables,
            next_round,
            history_str, user_interrupt, parliament_id,
        )
        for mid, (name, role) in member_info.items()
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
                "basis_level": initial_stances.get(
                    stmt.member_id, {}
                ).get("basis_level", "limited"),
                "duty": DUTY_LABELS.get(
                    debate_duties.get(stmt.member_id, ""),
                    "专业质询",
                ),
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
            speaker_name = member_info.get(speaker, ("该专家", ""))[0]
            err_str = str(err)

            # P0 修：区分两类失败
            # 1. 内容审核 (1301) / 真正的 LLM processed error → 议员写降级发言，不写"未及时回应"
            #    （被审核是系统性的，重试也是同样结果；写出"我维持立场无新增"让会议能继续）
            # 2. timeout / 网络抖动 → 真的是"未及时回应"
            is_content_filtered = "1301" in err_str or "BadRequest" in err_str or "contentFilter" in err_str

            if is_content_filtered:
                # 用该议员上一轮的立场生成一个"维持立场"的降级发言
                prev_entry = next(
                    (e for e in reversed(new_transcript)
                     if e.get("speaker") == speaker and e.get("content")),
                    None,
                )
                prev_stance = (prev_entry or {}).get("stance", "neutral")
                stance_label = {
                    "for": "支持", "against": "反对", "neutral": "保留意见"
                }.get(prev_stance, "保留意见")
                fallback_text = (
                    f"{speaker_name}：受到平台内容审核限制无法展开新的论证，"
                    f"我维持此前{stance_label}的立场。"
                )
                new_entries.append({
                    "speaker": speaker,
                    "round": next_round,
                    "type": "debate_degraded",
                    "stance": prev_stance,
                    "content": fallback_text,
                    "evidence": [],
                    "confidence": 0.3,
                    "degraded": True,
                    "degradation_reason": "content_filtered_1301",
                })
                tool_calls_log.append({
                    "member": speaker,
                    "action": "debate_degraded_content_filter",
                    "round": next_round,
                    "error": err_str[:200],
                    "timestamp": time.time(),
                })
                print(
                    f"[debate] CONTENT_FILTER (1301) parliament={parliament_id} "
                    f"member={speaker} round={next_round} → 标 degraded 继续",
                    flush=True,
                )
            else:
                # timeout / 网络 / 其他真 exception：
                # 改为议员本人的降级发言（标 type=debate_degraded），不再写 system_msg。
                # 这样 council_rounds.assess_third_round 不会因 debate_error 强制开第三轮，
                # 议会更可能干净收敛。
                prev_entry_for_speaker = next(
                    (e for e in reversed(new_transcript)
                     if e.get("speaker") == speaker and e.get("stance")
                     and e.get("type") not in ("debate_error", "debate_degraded")),
                    None,
                )
                prev_stance = (prev_entry_for_speaker or {}).get("stance", "neutral")
                stance_zh = {
                    "for": "支持", "against": "反对", "neutral": "保留意见"
                }.get(prev_stance, "保留意见")
                degraded_text = (
                    f"{speaker_name}：本轮时间或网络受限，未发表新的质疑或补充，"
                    f"维持此前{stance_zh}的立场。"
                )
                new_entries.append({
                    "speaker": speaker,
                    "round": next_round,
                    "type": "debate_degraded",
                    "stance": prev_stance,
                    "content": degraded_text,
                    "evidence": [],
                    "confidence": 0.3,
                    "degraded": True,
                    "degradation_reason": err_str[:200],
                })
                tool_calls_log.append({
                    "member": speaker,
                    "action": "debate_degraded",
                    "round": next_round,
                    "error": err_str[:200],
                    "timestamp": time.time(),
                })
                print(
                    f"[debate] DEGRADED parliament={parliament_id} "
                    f"member={speaker} round={next_round} → 维持 {prev_stance}",
                    flush=True,
                )

    # 张力
    stances = [e["stance"] for e in new_entries if e.get("stance")]
    unique_count = len(set(stances))
    tension = unique_count / 3.0

    new_members_state = dict(initial_stances)
    for entry in new_entries:
        member_id = entry.get("failed_speaker") or entry["speaker"]
        if member_id != "system":
            new_members_state[member_id] = entry

    return {
        "transcript": new_transcript + new_entries,
        "tool_calls": state.get("tool_calls", []) + tool_calls_log,
        "current_round": next_round,
        "tension_score": tension,
        "members": new_members_state,
    }
