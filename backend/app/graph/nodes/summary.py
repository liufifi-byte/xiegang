"""议长总结节点：基于辩论 + 选举结果，输出议会纪要。

议长 = 选举获胜者（已写入 state["election_result"]["winner"]）。

输出结构化议会纪要：
- consensus: 5 个议员都同意的
- dissent: 核心争议点 + 各方立场
- blind_spots: 议长补的盲区（本次辩论没人提到的视角）
- advice: 综合建议（标注"不是正确答案"）
- open_questions: 建议用户下一步去搞清楚的事
"""
import json
import re

from app.llm import get_llm
from app.agents.profiles import runtime_member_info


MEMBER_INFO: dict[str, tuple[str, str]] = {
    "suan": ("老算", "理性"),
    "chen": ("老沉", "保守"),
    "ji":   ("小机", "机会"),
    "nian": ("十年", "长期"),
    "fan":  ("反对", "反共识"),
}


SUMMARY_PROMPT = """你是邪杠议会当选议长。

## 补充守则
- 你必须先列出本次议会的"共识区"（≥1 条）
- 你必须列出"分歧区"（≥1 条）
- 你必须主动补一个"盲区"——本次辩论没人提到的视角
- 综合建议必须明确标注"这是综合建议，不是正确答案"
- 不要重复你作为议员时说过的话——你现在以议长身份说话
- 你的综合必须客观，不能偏向你原本的思维模型

## 议会信息
议题：{topic}
当选议长：{chair}（{chair_role}）
是否兜底任命：{fallback_note}

## 5 议员立场摘要
{members_summary}

## 辩论记录（最近 20 条）
{debate_summary}

## 输出（严格 JSON，不要 markdown、不要其他文字）
{{
  "consensus": ["共识1", "共识2"],
  "dissent": [
    {{"topic": "争议主题", "sides": {{"suan": "立场", "chen": "立场"}}}}
  ],
  "blind_spots": ["本次辩论没人提的视角1", "视角2"],
  "advice": "综合建议（包含'这是综合建议不是正确答案'）",
  "open_questions": ["建议用户下一步搞清楚的1", "2"]
}}"""


def _extract_json_from_response(text: str) -> dict:
    """从 LLM 回复里抠出 JSON（容错）。

    LLM 偶尔会包 ```json ... ``` 或前后带解释性文字。
    """
    text = text.strip()

    # 先尝试剥离 markdown fence
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    # 找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"无法在响应中找到 JSON 对象：{text[:100]}")

    json_str = text[start:end + 1]
    return json.loads(json_str)


def _fallback_summary(state: dict, error_msg: str) -> dict:
    """LLM 失败时的兜底纪要结构。"""
    members_summary = "\n".join([
        f"- [{mid}] 立场={data.get('stance', '?')}: "
        f"{(data.get('reasoning_summary') or data.get('content') or '')[:100]}"
        for mid, data in state.get("members", {}).items()
    ])
    return {
        "consensus": [],
        "dissent": [],
        "blind_spots": [f"[总结生成失败 {error_msg}]，建议人工审阅辩论记录"],
        "advice": (
            f"本次议会总结因技术原因未能生成（{error_msg}）。"
            "请参考 5 议员的辩论记录，自行综合。"
            "注意：以上仅为兜底信息，不是建议。"
        ),
        "open_questions": list(state.get("members", {}).keys()),
        "members_summary_for_human": members_summary,
    }


def run_summary(state: dict) -> dict:
    """LangGraph 节点：议长做总结。"""
    election = state.get("election_result") or {}
    chair_id = election.get("winner", "fan")
    fallback_used = election.get("fallback_used", False)

    member_info = runtime_member_info(state, MEMBER_INFO)
    chair_name, chair_role = member_info.get(chair_id, ("会议主持", "综合议会观点"))

    # 5 议员立场摘要
    members_lines = []
    for mid, data in state.get("members", {}).items():
        stance = data.get("stance", "?")
        confidence = data.get("confidence", 0)
        summary_text = (
            data.get("reasoning_summary") or data.get("content") or ""
        )[:200]
        members_lines.append(
            f"- [{mid}] 立场={stance} 置信={confidence}: {summary_text}"
        )
    members_summary = "\n".join(members_lines) or "（无）"

    # 辩论记录（最近 20 条）
    debate_lines = []
    for entry in state.get("transcript", [])[-20:]:
        if entry.get("type") in ("debate", "research"):
            speaker = entry.get("speaker", "?")
            r = entry.get("round", 0)
            content = (entry.get("content") or "")[:200]
            stance = entry.get("stance", "")
            debate_lines.append(f"[{speaker} R{r} | {stance}]: {content}")
    debate_summary = "\n".join(debate_lines) or "（无）"

    prompt = SUMMARY_PROMPT.format(
        topic=state["topic"],
        chair=chair_name,
        chair_role=chair_role,
        fallback_note="是（选举失败后兜底任命）" if fallback_used else "否",
        members_summary=members_summary,
        debate_summary=debate_summary,
    )

    try:
        llm = get_llm(temperature=0.5, max_tokens=3000)
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        summary = _extract_json_from_response(content)

        # 校验必填字段
        for key in ("consensus", "dissent", "blind_spots", "advice", "open_questions"):
            if key not in summary:
                summary[key] = [] if key != "advice" else ""

        # 强制 advice 含"不是正确答案"声明
        if "不是正确答案" not in (summary.get("advice") or ""):
            summary["advice"] = (
                (summary.get("advice") or "（综合各方视角）")
                + "（明确声明：这是综合建议，不是正确答案）"
            )

    except Exception as e:
        summary = _fallback_summary(state, str(e))

    # 元数据
    summary["chair"] = chair_id
    summary["chair_name"] = chair_name
    summary["fallback_used"] = fallback_used

    # V1-S1.2: 把完整选举结果 + 议员立场分布一并塞进 summary，供前端展示
    election = state.get("election_result")
    if election:
        summary["election"] = election

    members = state.get("members", {})
    if members:
        member_stances = {}
        for mid, data in members.items():
            stance = data.get("stance")
            if stance in ("for", "against", "neutral"):
                member_stances[mid] = {
                    "stance": stance,
                    "confidence": float(data.get("confidence", 0.0)),
                }
        if member_stances:
            summary["member_stances"] = member_stances

    return {"summary": summary}
