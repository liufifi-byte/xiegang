"""Runtime governance for a bounded expert ReAct agent."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from app.agents.evidence import EvidenceLedger
from app.agents.profiles import ExpertProfile
from app.agents.safety import ObservationNormalizer, sanitize_public_error, sanitize_query
from app.agents.schemas import (
    AgentTraceEvent,
    ExpertResearchResult,
    SafeActionRecord,
    ToolObservation,
    ToolRequest,
)
from app.agents.tool_registry import ToolContext, ToolRegistry
from app.agents.trace import AgentTraceSink, TraceRenderer, source_previews


class AgentCancelled(RuntimeError):
    pass


class AgentBudgetExceeded(RuntimeError):
    pass


async def _never_cancelled() -> bool:
    return False


@dataclass
class AgentBudget:
    max_reason_steps: int = 3
    max_tool_calls: int = 2
    tool_timeout_seconds: int = 15
    total_timeout_seconds: int = 60
    max_observation_chars: int = 4000
    max_evidence: int = 3

    reason_steps_used: int = 0
    tool_calls_used: int = 0
    started_at: float = 0.0

    def start(self) -> None:
        if self.started_at <= 0:
            self.started_at = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return 0.0 if self.started_at <= 0 else time.monotonic() - self.started_at

    def has_time(self) -> bool:
        return self.elapsed_seconds < self.total_timeout_seconds

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.total_timeout_seconds - self.elapsed_seconds)

    def can_reason(self) -> bool:
        return self.has_time() and self.reason_steps_used < self.max_reason_steps

    def can_use_tool(self) -> bool:
        return self.has_time() and self.tool_calls_used < self.max_tool_calls


class AgentHarness:
    def __init__(
        self,
        *,
        parliament_id: str,
        profile: ExpertProfile,
        registry: ToolRegistry,
        context: ToolContext,
        trace_sink: AgentTraceSink,
        budget: AgentBudget | None = None,
        sensitive_values: list[str] | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
        run_id: str | None = None,
    ):
        self.parliament_id = parliament_id
        self.profile = profile
        self.registry = registry
        self.context = context
        self.trace_sink = trace_sink
        self.budget = budget or AgentBudget()
        self.sensitive_values = sensitive_values or []
        self.cancel_check = cancel_check or _never_cancelled
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        self.trace_id = f"trace_{uuid.uuid4().hex[:12]}"
        self.ledger = EvidenceLedger(profile.expert_id, self.budget.max_evidence)
        self.normalizer = ObservationNormalizer(self.budget.max_observation_chars)
        self.safe_actions: list[SafeActionRecord] = []

    async def _check_cancelled(self) -> None:
        if await self.cancel_check():
            raise AgentCancelled("agent cancelled by user")

    async def _emit(
        self,
        phase: str,
        step: int,
        *,
        request: ToolRequest | None = None,
        observation: ToolObservation | None = None,
        evidence_count: int | None = None,
        degraded: bool = False,
    ) -> None:
        public_error = observation.public_error if observation and not observation.ok else None
        event = AgentTraceEvent(
            trace_id=self.trace_id,
            run_id=self.run_id,
            agent_id=self.profile.expert_id,
            step=step,
            phase="degraded" if degraded else phase,
            public_message=TraceRenderer.message(
                "degraded" if degraded else phase,
                self.profile,
                tool_name=request.tool_name if request else None,
                result_count=len(observation.results) if observation else 0,
                evidence_count=evidence_count or 0,
                error=public_error,
            ),
            public_intent=request.public_intent if request else None,
            tool_name=request.tool_name if request else None,
            display_query=(
                str(request.arguments.get("query"))
                if request and request.tool_name in {"web_search", "memory_search"}
                else None
            ),
            source_previews=source_previews(observation) if observation else [],
            evidence_count=evidence_count,
            duration_ms=observation.duration_ms if observation else None,
            cache_hit=observation.cache_hit if observation else None,
            timestamp=time.time(),
        )
        await self.trace_sink.emit(event)

    async def start(self) -> None:
        self.budget.start()
        await self._check_cancelled()
        await self._emit("started", 0)

    async def before_reason(self) -> int:
        await self._check_cancelled()
        if not self.budget.can_reason():
            raise AgentBudgetExceeded("reason budget exhausted")
        self.budget.reason_steps_used += 1
        step = self.budget.reason_steps_used
        await self._emit("deciding", step)
        return step

    def _sanitize_tool_request(self, request: ToolRequest) -> ToolRequest:
        if request.tool_name not in {"web_search", "memory_search"}:
            return request
        arguments = dict(request.arguments)
        arguments["query"] = sanitize_query(
            str(arguments.get("query") or ""), self.sensitive_values
        )
        return request.model_copy(update={"arguments": arguments})

    async def execute_tool(self, request: ToolRequest, step: int) -> tuple[ToolRequest, ToolObservation]:
        await self._check_cancelled()
        if not self.budget.can_use_tool():
            raise AgentBudgetExceeded("tool budget exhausted")
        safe_request = self._sanitize_tool_request(request)
        await self._emit("tool_started", step, request=safe_request)
        started = time.monotonic()
        try:
            observation = await asyncio.wait_for(
                self.registry.execute(
                    self.profile,
                    safe_request.tool_name,
                    safe_request.arguments,
                    self.context,
                ),
                timeout=self.budget.tool_timeout_seconds,
            )
        except TimeoutError as exc:
            observation = ToolObservation(
                tool_call_id=f"timeout_{uuid.uuid4().hex[:8]}",
                tool_name=safe_request.tool_name,
                ok=False,
                error_code="tool_timeout",
                public_error=sanitize_public_error(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        self.budget.tool_calls_used += 1
        observation = self.normalizer.normalize(observation)
        self.safe_actions.append(
            SafeActionRecord(
                step=step,
                action="tool_call",
                tool_name=safe_request.tool_name,
                arguments_summary=safe_request.arguments,
                ok=observation.ok,
                duration_ms=observation.duration_ms,
                timestamp=time.time(),
            )
        )
        await self._emit(
            "tool_completed", step, request=safe_request, observation=observation
        )
        added = self.ledger.add_from_observation(observation)
        if added:
            await self._emit(
                "evidence_added",
                step,
                request=safe_request,
                evidence_count=len(self.ledger.records),
            )
        return safe_request, observation

    async def before_finalize(self, step: int) -> None:
        await self._check_cancelled()
        await self._emit("finalizing", step)

    def validate_result(
        self,
        result: ExpertResearchResult,
        *,
        forced_reason: str | None = None,
    ) -> ExpertResearchResult:
        known_refs = [ref for ref in result.evidence_refs if self.ledger.contains(ref)]
        invented = len(known_refs) != len(result.evidence_refs)
        degradation_reasons = [reason for reason in [forced_reason] if reason]
        if invented:
            degradation_reasons.append("移除了无法追溯的外部证据引用")
        degraded = result.degraded or bool(degradation_reasons)
        reason = result.degradation_reason
        if degradation_reasons:
            reason = "；".join(degradation_reasons)
        records = [record for record in self.ledger.records if record.evidence_id in known_refs]
        auditable_count = sum(
            record.kind in {"web_source", "calculation", "user_fact"}
            for record in records
        )
        if auditable_count >= 2 and not result.degraded:
            basis_level = "strong"
        elif auditable_count >= 1:
            basis_level = "moderate"
        else:
            basis_level = "limited"
        # V3：新 stance 枚举（support/oppose/cautious/neutral）——lens 一致性兜底
        lens_title = result.lens_title or self.profile.lens_title
        return result.model_copy(
            update={
                "agent_id": self.profile.expert_id,
                "evidence_refs": known_refs,
                "tool_calls_used": self.budget.tool_calls_used,
                "degraded": degraded,
                "degradation_reason": reason if degraded else None,
                "confidence": min(result.confidence, 0.6) if degraded else result.confidence,
                "lens_title": lens_title,
                "basis_level": basis_level,
            }
        )

    async def complete(self, result: ExpertResearchResult) -> None:
        await self._emit(
            "completed" if not result.degraded else "degraded",
            self.budget.reason_steps_used,
            evidence_count=len(self.ledger.records),
            degraded=result.degraded,
        )

    async def failed(self) -> None:
        await self._emit("failed", self.budget.reason_steps_used)
