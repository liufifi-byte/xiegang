"""Model adapters for native tool calling with a structured-action fallback."""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.agents.schemas import AgentDecision, ExpertResearchResult, ToolRequest
from app.llm import extract_json_from_text, get_llm, get_structured_llm
from app.llm_concurrency import get_llm_semaphore


class DecisionAdapter(Protocol):
    async def decide(
        self,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        *,
        force_finish: bool = False,
    ) -> AgentDecision: ...


class NativeToolDecisionAdapter:
    async def decide(
        self,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        *,
        force_finish: bool = False,
    ) -> AgentDecision:
        llm = get_llm(temperature=0.3, max_tokens=1800)
        if force_finish:
            submit_tool = {
                "type": "function",
                "function": {
                    "name": "submit_expert_result",
                    "description": "提交专家最终研究结论",
                    "parameters": ExpertResearchResult.model_json_schema(),
                },
            }
            llm = llm.bind_tools(
                [submit_tool],
                tool_choice="submit_expert_result",
            )
        else:
            openai_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": schema["name"],
                        "description": schema["description"],
                        "parameters": schema["parameters"],
                    },
                }
                for schema in tool_schemas
            ]
            llm = llm.bind_tools(openai_tools)
        async with get_llm_semaphore():
            response = await llm.ainvoke(prompt)
        tool_calls = getattr(response, "tool_calls", None) or []
        if tool_calls and force_finish:
            call = tool_calls[0]
            name = call.get("name") if isinstance(call, dict) else call.name
            args = call.get("args") if isinstance(call, dict) else call.args
            if name != "submit_expert_result":
                raise ValueError("forced finalization returned an unexpected tool")
            return AgentDecision(
                action="finish",
                result=ExpertResearchResult.model_validate(args or {}),
            )
        if tool_calls:
            call = tool_calls[0]
            name = call.get("name") if isinstance(call, dict) else call.name
            args = call.get("args") if isinstance(call, dict) else call.args
            intent = {
                "web_search": "verify_public_fact",
                "calculator": "quantify_tradeoff",
                "memory_search": "review_user_history",
            }.get(name, "fill_evidence_gap")
            return AgentDecision(
                action="tool",
                tool_request=ToolRequest(
                    tool_name=name,
                    arguments=args or {},
                    public_intent=intent,
                ),
            )
        content = response.content if hasattr(response, "content") else str(response)
        payload = json.loads(extract_json_from_text(content))
        if payload.get("action") == "finish":
            return AgentDecision.model_validate(payload)
        return AgentDecision(action="finish", result=ExpertResearchResult.model_validate(payload))


class StructuredDecisionAdapter:
    async def decide(
        self,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        *,
        force_finish: bool = False,
    ) -> AgentDecision:
        if force_finish:
            structured_result = get_structured_llm(
                ExpertResearchResult, temperature=0.3
            )
            async with get_llm_semaphore():
                result = await structured_result.ainvoke(
                    prompt + "\n工具已禁用。只返回最终 ExpertResearchResult。"
                )
            return AgentDecision(action="finish", result=result)

        suffix = "\n你可以自主选择一个工具，或在信息足够时直接 finish。"
        structured = get_structured_llm(AgentDecision, temperature=0.3)
        async with get_llm_semaphore():
            decision = await structured.ainvoke(prompt + suffix)
        return decision


class ResilientDecisionAdapter:
    def __init__(
        self,
        native: DecisionAdapter | None = None,
        fallback: DecisionAdapter | None = None,
    ):
        self.native = native or NativeToolDecisionAdapter()
        self.fallback = fallback or StructuredDecisionAdapter()

    async def decide(
        self,
        prompt: str,
        tool_schemas: list[dict[str, Any]],
        *,
        force_finish: bool = False,
    ) -> AgentDecision:
        try:
            return await self.native.decide(
                prompt,
                [] if force_finish else tool_schemas,
                force_finish=force_finish,
            )
        except Exception:
            return await self.fallback.decide(
                prompt,
                [] if force_finish else tool_schemas,
                force_finish=force_finish,
            )
