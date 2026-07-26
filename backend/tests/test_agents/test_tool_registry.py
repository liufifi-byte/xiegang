import pytest
from pydantic import BaseModel

from app.agents.profiles import get_expert_profile
from app.agents.schemas import ToolResultItem
from app.agents.tool_registry import RegisteredTool, ToolContext, ToolRegistry


class Args(BaseModel):
    value: str


async def fake_handler(args: BaseModel, context: ToolContext):
    parsed = Args.model_validate(args)
    return [ToolResultItem(title=context.expert_id, content=parsed.value)]


@pytest.mark.asyncio
async def test_registry_enforces_permissions_and_validates_arguments():
    registry = ToolRegistry(
        tools=[RegisteredTool("calculator", Args, fake_handler)]
    )
    context = ToolContext("psychologist", None, "general")
    denied = await registry.execute(
        get_expert_profile("psychologist"), "calculator", {"value": "1"}, context
    )
    assert denied.error_code == "tool_not_allowed"

    context = ToolContext("mathematician", None, "general")
    invalid = await registry.execute(
        get_expert_profile("mathematician"), "calculator", {}, context
    )
    assert invalid.error_code == "invalid_arguments"


@pytest.mark.asyncio
async def test_registry_reuses_cache_inside_one_instance():
    calls = 0

    async def counting_handler(args: BaseModel, context: ToolContext):
        nonlocal calls
        calls += 1
        return await fake_handler(args, context)

    registry = ToolRegistry(
        tools=[RegisteredTool("calculator", Args, counting_handler)]
    )
    profile = get_expert_profile("mathematician")
    context = ToolContext("mathematician", None, "general")
    first = await registry.execute(profile, "calculator", {"value": "42"}, context)
    second = await registry.execute(profile, "calculator", {"value": "42"}, context)
    assert first.ok and second.ok
    assert second.cache_hit is True
    assert calls == 1


@pytest.mark.asyncio
async def test_anonymous_agent_cannot_see_or_call_personal_memory_tool():
    registry = ToolRegistry()
    profile = get_expert_profile("economist")
    anonymous_context = ToolContext("economist", None, "offer")
    schemas = registry.schemas_for(profile, anonymous_context)
    assert "memory_search" not in {schema["name"] for schema in schemas}

    denied = await registry.execute(
        profile,
        "memory_search",
        {"query": "过去的工作决策", "limit": 3},
        anonymous_context,
    )
    assert denied.ok is False
    assert denied.error_code == "memory_requires_user"

    signed_in_context = ToolContext("economist", "user-1", "offer")
    signed_in_schemas = registry.schemas_for(profile, signed_in_context)
    assert "memory_search" in {schema["name"] for schema in signed_in_schemas}


@pytest.mark.asyncio
async def test_failed_web_search_opens_circuit_for_rest_of_meeting():
    calls = 0

    async def unavailable_handler(args: BaseModel, context: ToolContext):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    registry = ToolRegistry(
        tools=[RegisteredTool("web_search", Args, unavailable_handler)]
    )
    profile = get_expert_profile("economist")
    context = ToolContext("economist", None, "offer")

    first = await registry.execute(
        profile, "web_search", {"value": "query one"}, context
    )
    second = await registry.execute(
        profile, "web_search", {"value": "query two"}, context
    )

    assert first.ok is False
    assert second.error_code == "tool_unavailable"
    assert "推断假设" in second.public_error
    assert calls == 1
