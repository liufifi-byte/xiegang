"""Permissioned real tools for expert research agents."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, ValidationError

from app.agents.profiles import ExpertProfile
from app.agents.schemas import ToolObservation, ToolResultItem


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    search_focus: str = Field(default="", max_length=80)
    max_results: int = Field(default=5, ge=1, le=5)


class CalculatorArgs(BaseModel):
    expression: str = Field(min_length=1, max_length=500)
    variables: dict[str, float] = Field(default_factory=dict)


class MemorySearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=3, ge=1, le=3)


@dataclass(frozen=True)
class ToolContext:
    expert_id: str
    user_id: str | None
    category: str


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[list[ToolResultItem]]]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    args_model: type[BaseModel]
    handler: ToolHandler


class ParliamentToolCache:
    """A cache intentionally scoped to one parliament run."""

    def __init__(self):
        self._values: dict[str, ToolObservation] = {}

    @staticmethod
    def key(tool_name: str, arguments: dict[str, Any]) -> str:
        return f"{tool_name}:{json.dumps(arguments, sort_keys=True, ensure_ascii=False)}"

    def get(self, tool_name: str, arguments: dict[str, Any]) -> ToolObservation | None:
        return self._values.get(self.key(tool_name, arguments))

    def set(self, tool_name: str, arguments: dict[str, Any], value: ToolObservation) -> None:
        self._values[self.key(tool_name, arguments)] = value


async def _web_search_handler(args: BaseModel, _: ToolContext) -> list[ToolResultItem]:
    from app.tools.web_search import web_search

    parsed = WebSearchArgs.model_validate(args)
    query = parsed.query
    if parsed.search_focus:
        query = f"{query} {parsed.search_focus}"
    raw = await asyncio.to_thread(web_search, query, parsed.max_results)
    return [
        ToolResultItem(
            title=str(item.get("title") or "公开资料"),
            url=item.get("url"),
            content=str(item.get("content") or ""),
            score=item.get("score"),
            metadata={
                "media": item.get("media"),
                "publish_date": item.get("publish_date"),
            },
        )
        for item in raw[: parsed.max_results]
    ]


async def _calculator_handler(args: BaseModel, _: ToolContext) -> list[ToolResultItem]:
    from app.tools.calculator import safe_calculate

    parsed = CalculatorArgs.model_validate(args)
    value = safe_calculate(parsed.expression, parsed.variables)
    return [
        ToolResultItem(
            title="安全计算结果",
            content=f"{parsed.expression} = {value}",
            score=1.0,
            metadata={
                "inputs": parsed.variables or {"expression": parsed.expression},
                "formula": parsed.expression,
                "result": value,
            },
        )
    ]


async def _memory_search_handler(args: BaseModel, context: ToolContext) -> list[ToolResultItem]:
    from app.tools.memory_query import memory_query_async

    parsed = MemorySearchArgs.model_validate(args)
    content = await memory_query_async(
        topic_category=context.category,
        query_text=parsed.query,
        limit=parsed.limit,
        user_id=context.user_id,
    )
    return [
        ToolResultItem(
            title="用户提供的相关历史决策",
            content=content,
            score=0.7,
            metadata={"source_kind": "user_fact"},
        )
    ]


class ToolRegistry:
    def __init__(
        self,
        tools: list[RegisteredTool] | None = None,
        cache: ParliamentToolCache | None = None,
    ):
        tools = tools or [
            RegisteredTool("web_search", WebSearchArgs, _web_search_handler),
            RegisteredTool("calculator", CalculatorArgs, _calculator_handler),
            RegisteredTool("memory_search", MemorySearchArgs, _memory_search_handler),
        ]
        self._tools = {tool.name: tool for tool in tools}
        self.cache = cache or ParliamentToolCache()
        self._unavailable_tools: set[str] = set()

    def schemas_for(
        self,
        profile: ExpertProfile,
        context: ToolContext | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": {
                    "web_search": "搜索需要外部核实的公开事实或案例",
                    "calculator": "执行安全数值和情景计算",
                    "memory_search": "检索该用户过去的相关决策",
                }[name],
                "parameters": self._tools[name].args_model.model_json_schema(),
            }
            for name in profile.allowed_tools
            if name in self._tools
            and not (
                name == "memory_search"
                and (context is None or context.user_id is None)
            )
        ]

    async def execute(
        self,
        profile: ExpertProfile,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolObservation:
        call_id = "tool_" + uuid.uuid4().hex[:12]
        started = time.monotonic()
        if tool_name not in self._tools:
            return ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error_code="unknown_tool",
                public_error="请求的工具不存在",
            )
        if tool_name in self._unavailable_tools:
            return ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error_code="tool_unavailable",
                public_error=(
                    "网页来源暂未核实，本轮仅使用用户信息、计算结果与明确标注的推断假设"
                    if tool_name == "web_search"
                    else "该工具本轮暂时不可用"
                ),
            )
        if tool_name not in profile.allowed_tools:
            return ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error_code="tool_not_allowed",
                public_error="该专家无权使用此工具",
            )
        if tool_name == "memory_search" and context.user_id is None:
            return ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error_code="memory_requires_user",
                public_error="登录后才能检索个人历史决策",
            )
        tool = self._tools[tool_name]
        try:
            parsed = tool.args_model.model_validate(arguments)
        except ValidationError:
            return ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error_code="invalid_arguments",
                public_error="工具参数不完整，专家可以重新调整",
            )

        normalized_args = parsed.model_dump()
        cached = self.cache.get(tool_name, normalized_args)
        if cached is not None:
            return cached.model_copy(update={"tool_call_id": call_id, "cache_hit": True})
        try:
            results = await tool.handler(parsed, context)
            if tool_name == "calculator" and isinstance(parsed, CalculatorArgs):
                calculator_args = parsed
                results = [
                    item.model_copy(
                        update={
                            "metadata": {
                                **item.metadata,
                                "inputs": calculator_args.variables
                                or {"expression": calculator_args.expression},
                                "formula": calculator_args.expression,
                            }
                        }
                    )
                    for item in results
                ]
            observation = ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=True,
                results=results,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            if tool_name == "web_search":
                self._unavailable_tools.add(tool_name)
            observation = ToolObservation(
                tool_call_id=call_id,
                tool_name=tool_name,
                ok=False,
                error_code="tool_failed",
                public_error=(
                    "网页来源暂未核实，本轮仅使用用户信息、计算结果与明确标注的推断假设"
                    if tool_name == "web_search"
                    else "工具暂时不可用，专家将使用现有信息继续分析"
                ),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        self.cache.set(tool_name, normalized_args, observation)
        return observation
