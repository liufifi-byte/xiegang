"""Decision Agent Runtime V4（spec 2026-08-22）。

确定性 Workflow 外壳 + 主 Agent Loop + 子 Agent Loop + 争议驱动 Debate Loop。
Phase 0：契约（schemas）、Runtime 协议（runtime）与 Fake 实现 + 评测基线；
Phase 1：Codex App Server 适配器（codex_runtime）、持久化仓库（repository，
migration 002）、事件桥（event_bridge）、INTAKE→PLANNING_LOOP 竖切（workflow）。
默认 DECISION_RUNTIME_MODE=legacy，不改变生产行为。
"""
from app.decision_harness.codex_runtime import CodexAgentRuntime
from app.decision_harness.event_bridge import (
    PRODUCT_EVENT_TYPES,
    make_product_event,
    translate_runtime_event,
)
from app.decision_harness.repository import DecisionRunRepository
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
from app.decision_harness.workflow import CHAIR_INSTRUCTIONS, DecisionWorkflow, WorkflowOutcome

__all__ = [
    "AgentRuntime",
    "AgentRuntimeError",
    "BudgetPolicy",
    "CHAIR_INSTRUCTIONS",
    "ContextPacket",
    "CodexAgentRuntime",
    "DebateTurnResponse",
    "DecisionModel",
    "DecisionRunRepository",
    "DecisionWorkflow",
    "DisputeIssue",
    "EvidenceRecordV4",
    "ExpertTaskPacket",
    "FakeAgentRuntime",
    "FinalReport",
    "MainAgentAction",
    "MainAgentActionRequest",
    "PRODUCT_EVENT_TYPES",
    "PlanSpec",
    "RunExitStatus",
    "RuntimeEvent",
    "RuntimeMode",
    "SubAgentResult",
    "WorkflowOutcome",
    "WorkflowPhase",
    "get_runtime_mode",
    "make_product_event",
    "translate_runtime_event",
]
