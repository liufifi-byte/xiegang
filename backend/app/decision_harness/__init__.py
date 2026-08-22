"""Decision Agent Runtime V4（spec 2026-08-22）。

确定性 Workflow 外壳 + 主 Agent Loop + 子 Agent Loop + 争议驱动 Debate Loop。
Phase 0：仅契约（schemas）、Runtime 协议（runtime）与 Fake 实现；
默认 DECISION_RUNTIME_MODE=legacy，不改变生产行为。
"""
from app.decision_harness.runtime import (
    AgentRuntime,
    AgentRuntimeError,
    FakeAgentRuntime,
    RuntimeEvent,
    RuntimeMode,
    get_runtime_mode,
)
from app.decision_harness.schemas import (
    BudgetPolicy,
    ContextPacket,
    DebateTurnResponse,
    DecisionModel,
    DisputeIssue,
    EvidenceRecordV4,
    ExpertTaskPacket,
    FinalReport,
    MainAgentAction,
    MainAgentActionRequest,
    PlanSpec,
    RunExitStatus,
    SubAgentResult,
    WorkflowPhase,
)

__all__ = [
    "AgentRuntime",
    "AgentRuntimeError",
    "BudgetPolicy",
    "ContextPacket",
    "DebateTurnResponse",
    "DecisionModel",
    "DisputeIssue",
    "EvidenceRecordV4",
    "ExpertTaskPacket",
    "FakeAgentRuntime",
    "FinalReport",
    "MainAgentAction",
    "MainAgentActionRequest",
    "PlanSpec",
    "RunExitStatus",
    "RuntimeEvent",
    "RuntimeMode",
    "SubAgentResult",
    "WorkflowPhase",
    "get_runtime_mode",
]
