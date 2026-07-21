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
