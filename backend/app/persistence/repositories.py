"""业务记录仓储。

Source、Evidence 和 Report 使用稳定主键与 merge 语义，保证 Checkpoint 重放时幂等。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import sessionmaker

from app.agents.contracts import ResearchCollection
from app.persistence.models import (
    CallAttemptModel,
    EvidenceCardModel,
    QuestionEvidenceModel,
    ReportClaimModel,
    ReportModel,
    ResearchDecisionModel,
    ResearchProgressModel,
    ResearchStepModel,
    ResearchTaskModel,
    ReviewModel,
    SearchRecordModel,
    SourceRecordModel,
    WorkflowEventModel,
)
from app.schemas.common import TaskStatus
from app.schemas.eval import ExecutionStats, QualityMetrics
from app.schemas.event import WorkflowEvent, WorkflowEventType
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.phase2 import CitationView, ResearchReportResponse
from app.schemas.progress import (
    ObservationStatus,
    ResearchActionStats,
    ResearchObservation,
    ResearchProgress,
)
from app.schemas.report import ReportClaim, ResearchReport
from app.schemas.research import ResearchPlan, ResearchTaskSnapshot, SearchRecord
from app.validators.delivery_self_check import run_delivery_self_check
from app.schemas.recovery import (
    DecisionStatus,
    OperationStatus,
    ResearchDecisionRecord,
    ResearchOperationRecord,
)
from app.schemas.review import ReviewResult
from app.schemas.usage import (
    CallAttempt,
    CallAttemptStatus,
    CallKind,
    ModelUsage,
    UsageMeasurement,
)


class ResearchRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def create_task(
        self,
        *,
        task_id: str,
        query: str,
        depth: str,
        workflow_version: str,
    ) -> None:
        with self._session_factory.begin() as session:
            session.add(
                ResearchTaskModel(
                    task_id=task_id,
                    query=query,
                    depth=depth,
                    status=TaskStatus.PENDING.value,
                    workflow_version=workflow_version,
                )
            )

    def acquire_run_lease(
        self, task_id: str, *, lease_seconds: float = 3600
    ) -> str | None:
        """Acquire a persistent single-run lease, reclaiming only stale leases."""

        now = datetime.now(timezone.utc)
        owner = f"RUN_{uuid4().hex[:12]}"
        with self._session_factory.begin() as session:
            result = session.execute(
                update(ResearchTaskModel)
                .where(
                    ResearchTaskModel.task_id == task_id,
                    or_(
                        ResearchTaskModel.run_owner.is_(None),
                        ResearchTaskModel.lease_expires_at.is_(None),
                        ResearchTaskModel.lease_expires_at <= now,
                    ),
                )
                .values(
                    run_owner=owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    run_attempt=ResearchTaskModel.run_attempt + 1,
                    updated_at=now,
                )
            )
            if result.rowcount == 0:
                if session.get(ResearchTaskModel, task_id) is None:
                    raise LookupError(f"Research task not found: {task_id}")
                return None
        return owner

    def owns_run_lease(self, task_id: str, owner: str) -> bool:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            task = session.get(ResearchTaskModel, task_id)
            return bool(
                task is not None
                and task.run_owner == owner
                and task.lease_expires_at is not None
                and _ensure_aware(task.lease_expires_at) > now
            )

    def renew_run_lease(
        self, task_id: str, owner: str, *, lease_seconds: float = 3600
    ) -> bool:
        """Renew only the current unexpired owner; never revive a stale lease."""
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            result = session.execute(
                update(ResearchTaskModel)
                .where(
                    ResearchTaskModel.task_id == task_id,
                    ResearchTaskModel.run_owner == owner,
                    ResearchTaskModel.lease_expires_at > now,
                )
                .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
            )
            return result.rowcount == 1

    def release_run_lease(self, task_id: str, owner: str) -> None:
        with self._session_factory.begin() as session:
            session.execute(
                update(ResearchTaskModel)
                .where(
                    ResearchTaskModel.task_id == task_id,
                    ResearchTaskModel.run_owner == owner,
                )
                .values(
                    run_owner=None, lease_expires_at=None,
                    updated_at=datetime.now(timezone.utc),
                )
            )

    def has_active_run(self, task_id: str) -> bool:
        now = datetime.now(timezone.utc)
        with self._session_factory() as session:
            task = session.get(ResearchTaskModel, task_id)
            return bool(
                task is not None
                and task.run_owner is not None
                and task.lease_expires_at is not None
                and _ensure_aware(task.lease_expires_at) > now
            )

    def update_status(
        self, task_id: str, status: TaskStatus, error: str | None = None
    ) -> None:
        with self._session_factory.begin() as session:
            task = session.get(ResearchTaskModel, task_id)
            if task is None:
                raise LookupError(f"Research task not found: {task_id}")
            task.status = status.value
            task.error_message = error
            task.updated_at = datetime.now(timezone.utc)

    def save_plan(self, task_id: str, plan: ResearchPlan) -> None:
        with self._session_factory.begin() as session:
            task = session.get(ResearchTaskModel, task_id)
            if task is None:
                raise LookupError(f"Research task not found: {task_id}")
            task.plan = plan.model_dump(mode="json")
            task.updated_at = datetime.now(timezone.utc)

    def save_research_progress(self, progress: ResearchProgress) -> None:
        with self._session_factory.begin() as session:
            if session.get(ResearchTaskModel, progress.task_id) is None:
                raise LookupError(
                    f"Research task not found: {progress.task_id}"
                )
            session.merge(_progress_model(progress))

    def record_research_observation(
        self,
        progress: ResearchProgress,
        observation: ResearchObservation,
    ) -> None:
        """Atomically persist a tool observation and its resulting progress."""

        if observation.step_id not in progress.tool_history_refs:
            raise ValueError("progress must reference the observation step_id")
        if observation.step_index != progress.step_index:
            raise ValueError("observation step_index must match progress")
        with self._session_factory.begin() as session:
            step = session.get(ResearchStepModel, observation.step_id)
            if step is None:
                step = ResearchStepModel(
                    step_id=observation.step_id,
                    task_id=progress.task_id,
                    step_index=observation.step_index,
                    tool_call_id=observation.tool_call_id,
                    observation=observation.model_dump(mode="json"),
                    created_at=observation.created_at,
                )
                session.add(step)
            else:
                step.step_index = observation.step_index
                step.tool_call_id = observation.tool_call_id
                step.observation = observation.model_dump(mode="json")
                step.operation_status = OperationStatus.COMMITTED.value
                step.updated_at = observation.created_at
            session.merge(_progress_model(progress))

    def save_research_decision(
        self,
        *,
        decision_id: str,
        task_id: str,
        conversation_ref: str,
        turn_index: int,
        input_messages: list[dict[str, object]],
        assistant_message: dict[str, object],
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            existing = session.get(ResearchDecisionModel, decision_id)
            if existing is None:
                session.add(
                    ResearchDecisionModel(
                        decision_id=decision_id,
                        task_id=task_id,
                        conversation_ref=conversation_ref,
                        turn_index=turn_index,
                        input_messages=input_messages,
                        assistant_message=assistant_message,
                        status=DecisionStatus.PENDING.value,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def complete_research_decision(self, decision_id: str) -> None:
        with self._session_factory.begin() as session:
            decision = session.get(ResearchDecisionModel, decision_id)
            if decision is None:
                raise LookupError(f"Research decision not found: {decision_id}")
            decision.status = DecisionStatus.COMPLETED.value
            decision.updated_at = datetime.now(timezone.utc)

    def get_research_decisions(
        self, task_id: str, conversation_ref: str
    ) -> list[ResearchDecisionRecord]:
        with self._session_factory() as session:
            models = list(
                session.scalars(
                    select(ResearchDecisionModel)
                    .where(
                        ResearchDecisionModel.task_id == task_id,
                        ResearchDecisionModel.conversation_ref == conversation_ref,
                    )
                    .order_by(ResearchDecisionModel.turn_index)
                )
            )
        return [_to_research_decision(item) for item in models]

    def start_research_operation(
        self,
        *,
        step_id: str,
        task_id: str,
        decision_id: str,
        turn_index: int,
        operation_index: int,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, object],
        step_index: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        with self._session_factory.begin() as session:
            if session.get(ResearchStepModel, step_id) is None:
                session.add(
                    ResearchStepModel(
                        step_id=step_id,
                        task_id=task_id,
                        step_index=step_index,
                        tool_call_id=tool_call_id,
                        decision_id=decision_id,
                        turn_index=turn_index,
                        operation_index=operation_index,
                        tool_name=tool_name,
                        arguments=arguments,
                        operation_status=OperationStatus.STARTED.value,
                        observation={},
                        created_at=now,
                        updated_at=now,
                    )
                )

    def save_research_operation_result(
        self, step_id: str, result_payload: dict[str, object]
    ) -> None:
        with self._session_factory.begin() as session:
            step = session.get(ResearchStepModel, step_id)
            if step is None:
                raise LookupError(f"Research operation not found: {step_id}")
            step.result_payload = result_payload
            step.operation_status = OperationStatus.RESULT_SAVED.value
            step.updated_at = datetime.now(timezone.utc)

    def mark_research_operation_incomplete(
        self, step_id: str, reason: str
    ) -> None:
        with self._session_factory.begin() as session:
            step = session.get(ResearchStepModel, step_id)
            if step is None:
                raise LookupError(f"Research operation not found: {step_id}")
            step.operation_status = OperationStatus.INCOMPLETE.value
            step.uncertainty_reason = reason
            step.updated_at = datetime.now(timezone.utc)

    def get_research_operations(
        self, decision_id: str
    ) -> list[ResearchOperationRecord]:
        with self._session_factory() as session:
            models = list(
                session.scalars(
                    select(ResearchStepModel)
                    .where(ResearchStepModel.decision_id == decision_id)
                    .order_by(ResearchStepModel.operation_index)
                )
            )
        return [_to_research_operation(item) for item in models]

    def get_research_progress(self, task_id: str) -> ResearchProgress | None:
        with self._session_factory() as session:
            model = session.get(ResearchProgressModel, task_id)
            return _to_progress(model) if model is not None else None

    def get_research_steps(
        self, task_id: str, *, limit: int = 20
    ) -> list[ResearchObservation]:
        with self._session_factory() as session:
            models = list(
                session.scalars(
                    select(ResearchStepModel)
                    .where(
                        ResearchStepModel.task_id == task_id,
                        ResearchStepModel.operation_status
                        == OperationStatus.COMMITTED.value,
                    )
                    .order_by(ResearchStepModel.step_index.desc())
                    .limit(limit)
                )
            )
        return [
            ResearchObservation.model_validate(item.observation)
            for item in reversed(models)
        ]

    def max_research_step_index(self, task_id: str) -> int:
        """已持久化的最大 step_index；没有步骤时返回 0。

        用于给新步骤分配**单调且不冲突**的索引。不能依赖 `ResearchProgress.step_index`：
        它是业务进度计数器，在恢复、重试或一轮多工具调用等路径上可能落后于
        `research_steps` 的实际内容，直接用 `progress.step_index + 1` 会撞上
        `UNIQUE(task_id, step_index)` 约束并让整个任务以 IntegrityError 终止。
        """

        with self._session_factory() as session:
            largest = session.scalar(
                select(func.max(ResearchStepModel.step_index)).where(
                    ResearchStepModel.task_id == task_id
                )
            )
        return int(largest or 0)

    def summarize_research_actions(self, task_id: str) -> ResearchActionStats:
        """只统计**已提交**的研究步骤。

        `start_research_operation` 会先写入 ``observation={}`` 占位；已开始但未提交
        （尚未写入 Observation）的步骤必须在统计时跳过。否则用空字典校验
        ``ResearchObservation`` 会抛 ``ValidationError``，并让整个调用方
        （例如评测执行器）失败，从而丢掉该任务的全部真实用量。
        """

        with self._session_factory() as session:
            rows = session.execute(
                select(
                    ResearchStepModel.observation,
                    ResearchStepModel.operation_status,
                ).where(ResearchStepModel.task_id == task_id)
            ).all()
        observations = [
            ResearchObservation.model_validate(observation)
            for observation, operation_status in rows
            if observation
            and operation_status == OperationStatus.COMMITTED.value
        ]
        return ResearchActionStats(
            external_tool_actions=sum(
                item.externally_executed for item in observations
            ),
            local_tool_actions=sum(
                not item.externally_executed and not item.invalid_action
                for item in observations
            ),
            invalid_tool_actions=sum(item.invalid_action for item in observations),
            cache_hits=sum(
                item.status == ObservationStatus.CACHE_HIT
                for item in observations
            ),
        )

    def get_source(self, source_id: str) -> SourceRecord | None:
        with self._session_factory() as session:
            model = session.get(SourceRecordModel, source_id)
            return _to_source(model) if model is not None else None

    def get_collection(self, task_id: str) -> ResearchCollection:
        with self._session_factory() as session:
            searches = list(
                session.scalars(
                    select(SearchRecordModel).where(
                        SearchRecordModel.task_id == task_id
                    )
                )
            )
            sources = list(
                session.scalars(
                    select(SourceRecordModel).where(
                        SourceRecordModel.task_id == task_id
                    )
                )
            )
            evidence = list(
                session.scalars(
                    select(EvidenceCardModel).where(
                        EvidenceCardModel.task_id == task_id
                    )
                )
            )
            links = list(
                session.scalars(
                    select(QuestionEvidenceModel).where(
                        QuestionEvidenceModel.task_id == task_id
                    )
                )
            )
        question_ids_by_evidence: dict[str, list[str]] = {}
        for link in links:
            question_ids_by_evidence.setdefault(link.evidence_id, []).append(
                link.question_id
            )
        return ResearchCollection(
            searches=[_to_search(item) for item in searches],
            sources=[_to_source(item) for item in sources],
            evidence=[
                _to_evidence(
                    item, question_ids_by_evidence.get(item.evidence_id)
                )
                for item in evidence
            ],
            rejected_evidence=[],
            errors=[],
            model_calls=0,
            tool_calls=0,
            search_calls=0,
            pages_read=0,
            progress=self.get_research_progress(task_id),
        )

    def save_execution_stats(
        self, task_id: str, execution: ExecutionStats
    ) -> None:
        """持久化任务用量与延迟，供报告、前端和评测统一读取。"""

        with self._session_factory.begin() as session:
            task = session.get(ResearchTaskModel, task_id)
            if task is None:
                raise LookupError(f"Research task not found: {task_id}")
            task.execution_stats = execution.model_dump(mode="json")
            task.latency_ms = execution.latency_ms
            task.updated_at = datetime.now(timezone.utc)

    def record_call_attempt(
        self,
        task_id: str,
        *,
        workflow_phase: str,
        call_kind: str,
        attempt_number: int,
        transition: str,
        payload: dict[str, object],
    ) -> None:
        """创建或更新一次真实调用尝试，不保存原始参数或密钥。"""

        attempt_id = str(payload["attempt_id"])
        logical_call_id = str(payload["logical_call_id"])
        status = {
            "started": CallAttemptStatus.STARTED,
            "completed": CallAttemptStatus.SUCCEEDED,
            "failed": CallAttemptStatus.FAILED,
        }[transition]
        with self._session_factory.begin() as session:
            attempt = session.get(CallAttemptModel, attempt_id)
            if attempt is None:
                attempt = CallAttemptModel(
                    attempt_id=attempt_id,
                    sequence=int(
                        session.scalar(
                            select(
                                func.coalesce(func.max(CallAttemptModel.sequence), 0)
                                + 1
                            )
                        )
                        or 1
                    ),
                    logical_call_id=logical_call_id,
                    task_id=task_id,
                    step_id=(
                        str(payload["step_id"])
                        if payload.get("step_id") is not None
                        else None
                    ),
                    attempt_number=attempt_number,
                    call_kind=call_kind,
                    phase=workflow_phase,
                    budget_phase=(
                        str(payload["budget_phase"])
                        if payload.get("budget_phase") is not None
                        else None
                    ),
                    operation_name=_operation_name(call_kind, payload),
                    arguments_hash=(
                        str(payload["arguments_hash"])
                        if payload.get("arguments_hash") is not None
                        else None
                    ),
                    status=status.value,
                    started_at=_parse_timestamp(payload.get("started_at")),
                )
                session.add(attempt)
            attempt.status = status.value
            attempt.ended_at = (
                _parse_timestamp(payload.get("ended_at"))
                if payload.get("ended_at") is not None
                else None
            )
            attempt.error_type = (
                str(payload["error_type"])
                if payload.get("error_type") is not None
                else None
            )
            attempt.result_ref = (
                str(payload["result_ref"])
                if payload.get("result_ref") is not None
                else None
            )
            usage = payload.get("usage")
            attempt.usage = dict(usage) if isinstance(usage, dict) else None
            reserved_prompt = payload.get("reserved_prompt_tokens")
            reserved_completion = payload.get("reserved_completion_tokens")
            if reserved_prompt is not None:
                attempt.reserved_prompt_tokens = int(reserved_prompt)
            if reserved_completion is not None:
                attempt.reserved_completion_tokens = int(reserved_completion)
            duration = payload.get("duration_ms")
            attempt.duration_ms = (
                float(duration) if isinstance(duration, int | float) else None
            )

    def get_call_attempts(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[CallAttempt]:
        with self._session_factory() as session:
            attempts = list(
                session.scalars(
                    select(CallAttemptModel)
                    .where(
                        CallAttemptModel.task_id == task_id,
                        CallAttemptModel.sequence > after_sequence,
                    )
                    .order_by(CallAttemptModel.sequence)
                    .limit(limit)
                )
            )
        return [_to_call_attempt(item) for item in attempts]

    def summarize_call_attempts(
        self, task_id: str, *, latency_ms: float
    ) -> ExecutionStats:
        """从调用账本汇总所有终态，包括失败与中断。"""

        attempts = self.get_call_attempts(task_id)
        model_attempts = [
            item for item in attempts if item.call_kind == CallKind.MODEL
        ]
        actual_attempts = [
            item
            for item in model_attempts
            if item.usage is not None
            and item.usage.measurement == UsageMeasurement.ACTUAL
        ]
        estimated_attempts = [
            item
            for item in model_attempts
            if item.usage is not None
            and item.usage.measurement == UsageMeasurement.ESTIMATED
        ]
        unknown_attempts = [
            item
            for item in model_attempts
            if item.usage is None
            or item.usage.measurement == UsageMeasurement.UNKNOWN
        ]
        actual_prompt_tokens = sum(
            item.usage.prompt_tokens or 0
            for item in actual_attempts
            if item.usage is not None
        )
        actual_completion_tokens = sum(
            item.usage.completion_tokens or 0
            for item in actual_attempts
            if item.usage is not None
        )
        charged_prompt_tokens = sum(
            (
                item.usage.prompt_tokens
                if item.usage is not None
                and item.usage.measurement == UsageMeasurement.ACTUAL
                and item.usage.prompt_tokens is not None
                else (item.reserved_prompt_tokens or 0)
            )
            for item in model_attempts
        )
        charged_completion_tokens = sum(
            (
                item.usage.completion_tokens
                if item.usage is not None
                and item.usage.measurement == UsageMeasurement.ACTUAL
                and item.usage.completion_tokens is not None
                else (item.reserved_completion_tokens or 0)
            )
            for item in model_attempts
        )
        measurements = {
            item.usage.measurement
            for item in model_attempts
            if item.usage is not None
        }
        if not model_attempts or not measurements:
            usage_measurement = "unknown"
        elif len(measurements) == 1 and not unknown_attempts:
            usage_measurement = next(iter(measurements)).value
        elif measurements == {UsageMeasurement.UNKNOWN}:
            usage_measurement = "unknown"
        else:
            usage_measurement = "mixed"

        known_costs = [
            item.usage.cost_amount
            for item in model_attempts
            if item.usage is not None and item.usage.cost_amount is not None
        ]
        currencies = {
            item.usage.cost_currency for item in model_attempts
            if item.usage is not None and item.usage.cost_amount is not None
        }
        all_costs_known = (
            bool(model_attempts)
            and len(known_costs) == len(model_attempts)
            and len(currencies) == 1
        )
        cost_amount = sum(known_costs) if all_costs_known else None
        return ExecutionStats(
            model_calls=len(model_attempts),
            tool_calls=sum(
                item.call_kind in {CallKind.SEARCH, CallKind.PAGE}
                for item in attempts
            ),
            search_calls=sum(
                item.call_kind == CallKind.SEARCH for item in attempts
            ),
            pages_read=sum(item.call_kind == CallKind.PAGE for item in attempts),
            prompt_tokens=charged_prompt_tokens,
            completion_tokens=charged_completion_tokens,
            actual_prompt_tokens=actual_prompt_tokens,
            actual_completion_tokens=actual_completion_tokens,
            actual_model_attempts=len(actual_attempts),
            estimated_model_attempts=len(estimated_attempts),
            unknown_model_attempts=len(unknown_attempts),
            usage_measurement=usage_measurement,
            estimated_cost=0,
            cost_is_estimated=bool(estimated_attempts or unknown_attempts),
            cost_amount=cost_amount,
            cost_currency=next(iter(currencies)) if all_costs_known else None,
            cost_reason=(
                None
                if all_costs_known and model_attempts
                else (
                    "no model attempts were recorded" if not model_attempts
                    else "cost currencies differ; no conversion was applied" if len(currencies) > 1
                    else "one or more model attempts have unknown cost"
                )
            ),
            latency_ms=latency_ms,
        )

    def append_event(
        self,
        task_id: str,
        event_type: WorkflowEventType,
        *,
        stage: str,
        payload: dict[str, object] | None = None,
    ) -> WorkflowEvent:
        """追加一个不可变工作流事件，供后续 SSE 和排错复用。"""

        with self._session_factory.begin() as session:
            sequence = session.scalar(
                select(func.coalesce(func.max(WorkflowEventModel.sequence), 0) + 1)
            )
            event = WorkflowEventModel(
                event_id=f"EVENT_{uuid4().hex[:12]}",
                sequence=int(sequence or 1),
                task_id=task_id,
                event_type=event_type.value,
                stage=stage,
                timestamp=datetime.now(timezone.utc),
                payload=payload or {},
            )
            session.add(event)
            session.flush()
            return _to_event(event)

    def get_events(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[WorkflowEvent]:
        """按单调序号读取事件，可用于 SSE 分页与断线续读。"""

        with self._session_factory() as session:
            models = list(
                session.scalars(
                    select(WorkflowEventModel)
                    .where(
                        WorkflowEventModel.task_id == task_id,
                        WorkflowEventModel.sequence > after_sequence,
                    )
                    .order_by(WorkflowEventModel.sequence)
                    .limit(limit)
                )
            )
            return [_to_event(item) for item in models]

    def get_latest_event(self, task_id: str) -> WorkflowEvent | None:
        """读取任务最后一个已持久化事件，用于判断 SSE 是否可以安全关闭。"""

        with self._session_factory() as session:
            model = session.scalar(
                select(WorkflowEventModel)
                .where(WorkflowEventModel.task_id == task_id)
                .order_by(WorkflowEventModel.sequence.desc())
                .limit(1)
            )
            return _to_event(model) if model is not None else None

    def save_collection(self, collection: ResearchCollection) -> None:
        with self._session_factory.begin() as session:
            for search in collection.searches:
                session.merge(
                    SearchRecordModel(
                        search_id=search.search_id,
                        task_id=search.task_id,
                        query=search.query,
                        result_urls=search.result_urls,
                        executed_at=search.executed_at,
                    )
                )
            source_task_ids: dict[str, str] = {}
            for source in collection.sources:
                source_task_ids[source.source_id] = source.task_id
                session.merge(
                    SourceRecordModel(
                        source_id=source.source_id,
                        task_id=source.task_id,
                        search_id=source.search_id,
                        title=source.title,
                        url=source.url,
                        normalized_url=source.normalized_url or source.url,
                        source_type=source.source_type.value,
                        retrieved_at=source.retrieved_at,
                        content_hash=source.content_hash,
                        raw_content=source.raw_content,
                        normalized_content=source.normalized_content,
                        url_fragment=source.url_fragment,
                        content_type=source.content_type,
                        extraction_version=source.extraction_version,
                        published_at=source.published_at,
                        source_updated_at=source.source_updated_at,
                        source_type_reason=source.source_type_reason,
                    )
                )
            for evidence in collection.evidence:
                task_id = source_task_ids[evidence.source_id]
                session.merge(
                    EvidenceCardModel(
                        evidence_id=evidence.evidence_id,
                        task_id=task_id,
                        question_id=evidence.question_id,
                        evidence_summary=evidence.evidence_summary,
                        quote=evidence.quote,
                        quote_hash=hashlib.sha256(
                            evidence.quote.encode("utf-8")
                        ).hexdigest(),
                        source_id=evidence.source_id,
                        locator=evidence.locator,
                        start_offset=evidence.start_offset,
                        end_offset=evidence.end_offset,
                        support_type=evidence.support_type.value,
                        source_quality=evidence.source_quality.value,
                    )
                )
                for question_id in evidence.question_ids:
                    session.merge(
                        QuestionEvidenceModel(
                            task_id=task_id,
                            question_id=question_id,
                            evidence_id=evidence.evidence_id,
                        )
                    )

    def save_report(
        self,
        *,
        task_id: str,
        workflow_version: str,
        report: ResearchReport,
        review: ReviewResult,
        metrics: QualityMetrics,
        revision_count: int = 0,
    ) -> str:
        report_id = f"REPORT_{task_id}_{revision_count}"
        with self._session_factory.begin() as session:
            session.merge(
                ReportModel(
                    report_id=report_id,
                    task_id=task_id,
                    workflow_version=workflow_version,
                    revision_count=revision_count,
                    title=report.title,
                    executive_summary=report.executive_summary,
                    comparison_table=report.comparison_table,
                    recommendation=report.recommendation,
                    risks_and_limitations=report.risks_and_limitations,
                    unverified_notice=report.unverified_notice,
                    metrics=metrics.model_dump(mode="json"),
                )
            )
            incoming_claim_ids = {claim.claim_id for claim in report.claims}
            stale_claims = list(
                session.scalars(
                    select(ReportClaimModel).where(
                        ReportClaimModel.report_id == report_id
                    )
                )
            )
            for stale_claim in stale_claims:
                if stale_claim.claim_id not in incoming_claim_ids:
                    session.delete(stale_claim)
            for claim in report.claims:
                session.merge(
                    ReportClaimModel(
                        claim_id=claim.claim_id,
                        report_id=report_id,
                        text=claim.text,
                        dimension=claim.dimension,
                        claim_type=claim.claim_type.value,
                        requires_citation=claim.requires_citation,
                        evidence_ids=claim.evidence_ids,
                        uncertainty=claim.uncertainty.value,
                    )
                )
            session.merge(
                ReviewModel(
                    review_id=f"REVIEW_{task_id}_{revision_count}",
                    task_id=task_id,
                    report_id=report_id,
                    revision_count=revision_count,
                    unsupported_claims=review.unsupported_claims,
                    overclaimed_claims=review.overclaimed_claims,
                    citation_issues=review.citation_issues,
                    missing_dimensions=review.missing_dimensions,
                    revision_instructions=review.revision_instructions,
                    claim_support_results=[
                        item.model_dump(mode="json")
                        for item in review.claim_support_results
                    ],
                    evidence_support_results=[
                        item.model_dump(mode="json")
                        for item in review.evidence_support_results
                    ],
                    requirement_checks=[
                        item.model_dump(mode="json")
                        for item in review.requirement_checks
                    ],
                    report_section_results=[
                        item.model_dump(mode="json")
                        for item in review.report_section_results
                    ],
                    gap_requests=[
                        item.model_dump(mode="json")
                        for item in review.gap_requests
                    ],
                    metrics=metrics.model_dump(mode="json"),
                )
            )
        return report_id

    def get_task(self, task_id: str) -> ResearchTaskSnapshot | None:
        with self._session_factory() as session:
            task = session.get(ResearchTaskModel, task_id)
            if task is None:
                return None
            snapshot = ResearchTaskSnapshot(
                task_id=task.task_id,
                query=task.query,
                status=TaskStatus(task.status),
                workflow_version=task.workflow_version,
                error=task.error_message,
                is_running=bool(
                    task.run_owner and task.lease_expires_at
                    and _ensure_aware(task.lease_expires_at) > datetime.now(timezone.utc)
                ),
                execution=_execution_from_task(task),
                created_at=_ensure_aware(task.created_at),
                updated_at=_ensure_aware(task.updated_at),
            )

        if self.get_call_attempts(task_id, limit=1):
            snapshot.execution = self.summarize_call_attempts(
                task_id, latency_ms=snapshot.execution.latency_ms
            )
        return snapshot

    def get_report(
        self, task_id: str, *, provider_notice: str
    ) -> ResearchReportResponse | None:
        with self._session_factory() as session:
            task = session.get(ResearchTaskModel, task_id)
            if task is None:
                return None
            report_model = session.scalar(
                select(ReportModel)
                .where(ReportModel.task_id == task_id)
                .order_by(ReportModel.revision_count.desc())
            )
            if report_model is None:
                return None
            claim_models = list(
                session.scalars(
                    select(ReportClaimModel).where(
                        ReportClaimModel.report_id == report_model.report_id
                    )
                )
            )
            review_model = session.scalar(
                select(ReviewModel).where(ReviewModel.report_id == report_model.report_id)
            )
            if review_model is None:
                return None
            evidence_models = list(
                session.scalars(
                    select(EvidenceCardModel).where(
                        EvidenceCardModel.task_id == task_id
                    )
                )
            )
            question_links = list(
                session.scalars(
                    select(QuestionEvidenceModel).where(
                        QuestionEvidenceModel.task_id == task_id
                    )
                )
            )
            source_models = list(
                session.scalars(
                    select(SourceRecordModel).where(SourceRecordModel.task_id == task_id)
                )
            )

            claims = [
                ReportClaim(
                    claim_id=item.claim_id,
                    text=item.text,
                    dimension=item.dimension,
                    claim_type=item.claim_type,
                    requires_citation=item.requires_citation,
                    evidence_ids=item.evidence_ids,
                    uncertainty=item.uncertainty,
                )
                for item in claim_models
            ]
            report = ResearchReport(
                title=report_model.title,
                executive_summary=report_model.executive_summary,
                claims=claims,
                comparison_table=report_model.comparison_table,
                recommendation=report_model.recommendation,
                risks_and_limitations=report_model.risks_and_limitations,
                unverified_notice=report_model.unverified_notice,
            )
            question_ids_by_evidence: dict[str, list[str]] = {}
            for link in question_links:
                question_ids_by_evidence.setdefault(link.evidence_id, []).append(
                    link.question_id
                )
            evidence = [
                _to_evidence(
                    item,
                    question_ids_by_evidence.get(item.evidence_id),
                )
                for item in evidence_models
            ]
            sources = [_to_source(item) for item in source_models]
            evidence_by_id = {item.evidence_id: item for item in evidence}
            source_by_id = {item.source_id: item for item in sources}
            citations: list[CitationView] = []
            for claim in claims:
                for evidence_id in claim.evidence_ids:
                    evidence_item = evidence_by_id[evidence_id]
                    source_item = source_by_id[evidence_item.source_id]
                    citations.append(
                        CitationView(
                            claim_id=claim.claim_id,
                            evidence_id=evidence_item.evidence_id,
                            source_id=source_item.source_id,
                            source_title=source_item.title,
                            quote=evidence_item.quote,
                            source_url=source_item.url,
                        )
                    )

            return ResearchReportResponse(
                task_id=task_id,
                status=TaskStatus(task.status),
                report=report,
                evidence=evidence,
                sources=sources,
                citations=citations,
                review=ReviewResult(
                    unsupported_claims=review_model.unsupported_claims,
                    overclaimed_claims=review_model.overclaimed_claims,
                    citation_issues=review_model.citation_issues,
                    missing_dimensions=review_model.missing_dimensions,
                    revision_instructions=review_model.revision_instructions,
                    claim_support_results=review_model.claim_support_results,
                    evidence_support_results=review_model.evidence_support_results,
                    requirement_checks=review_model.requirement_checks,
                    report_section_results=review_model.report_section_results,
                    gap_requests=review_model.gap_requests,
                ),
                metrics=QualityMetrics.model_validate(report_model.metrics),
                # 交付自检按需重算（纯确定性、零模型调用），因此永远与当前
                # 持久化数据一致，不需要新增表或迁移。
                delivery_self_check=run_delivery_self_check(
                    report, evidence, sources
                ),
                execution=_execution_from_task(task),
                provider_notice=provider_notice,
            )


def _ensure_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _progress_model(progress: ResearchProgress) -> ResearchProgressModel:
    payload = progress.model_dump(mode="json")
    return ResearchProgressModel(
        task_id=progress.task_id,
        schema_version=progress.schema_version,
        step_index=progress.step_index,
        questions=payload["questions"],
        coverage=payload["coverage"],
        open_gaps=payload["open_gaps"],
        conflicts=payload["conflicts"],
        source_refs=payload["source_refs"],
        evidence_refs=payload["evidence_refs"],
        tool_history_refs=payload["tool_history_refs"],
        conversation_ref=progress.conversation_ref,
        budget_remaining=payload["budget_remaining"],
        no_progress_streak=progress.no_progress_streak,
        stop_reason=progress.stop_reason,
        updated_at=progress.updated_at,
    )


def _to_progress(model: ResearchProgressModel) -> ResearchProgress:
    return ResearchProgress.model_validate(
        {
            "task_id": model.task_id,
            "schema_version": model.schema_version,
            "step_index": model.step_index,
            "questions": model.questions,
            "coverage": model.coverage,
            "open_gaps": model.open_gaps,
            "conflicts": model.conflicts,
            "source_refs": model.source_refs,
            "evidence_refs": model.evidence_refs,
            "tool_history_refs": model.tool_history_refs,
            "conversation_ref": model.conversation_ref,
            "budget_remaining": model.budget_remaining,
            "no_progress_streak": model.no_progress_streak,
            "stop_reason": model.stop_reason,
            "updated_at": _ensure_aware(model.updated_at),
        }
    )


def _to_evidence(
    model: EvidenceCardModel, question_ids: list[str] | None = None
) -> EvidenceCard:
    return EvidenceCard(
        evidence_id=model.evidence_id,
        question_id=model.question_id,
        question_ids=question_ids or [model.question_id],
        evidence_summary=model.evidence_summary,
        quote=model.quote,
        source_id=model.source_id,
        locator=model.locator,
        start_offset=model.start_offset,
        end_offset=model.end_offset,
        support_type=model.support_type,
        source_quality=model.source_quality,
    )


def _to_source(model: SourceRecordModel) -> SourceRecord:
    return SourceRecord(
        source_id=model.source_id,
        task_id=model.task_id,
        search_id=model.search_id,
        title=model.title,
        url=model.url,
        source_type=model.source_type,
        retrieved_at=_ensure_aware(model.retrieved_at),
        content_hash=model.content_hash,
        raw_content=model.raw_content,
        normalized_content=model.normalized_content,
        normalized_url=model.normalized_url,
        url_fragment=model.url_fragment,
        content_type=model.content_type,
        extraction_version=model.extraction_version or "readable-v1",
        published_at=(
            _ensure_aware(model.published_at)
            if model.published_at is not None
            else None
        ),
        source_updated_at=(
            _ensure_aware(model.source_updated_at)
            if model.source_updated_at is not None
            else None
        ),
        source_type_reason=model.source_type_reason,
    )


def _to_search(model: SearchRecordModel) -> SearchRecord:
    return SearchRecord(
        search_id=model.search_id,
        task_id=model.task_id,
        query=model.query,
        result_urls=model.result_urls,
        executed_at=_ensure_aware(model.executed_at),
    )


def _to_research_decision(
    model: ResearchDecisionModel,
) -> ResearchDecisionRecord:
    return ResearchDecisionRecord(
        decision_id=model.decision_id,
        task_id=model.task_id,
        conversation_ref=model.conversation_ref,
        turn_index=model.turn_index,
        input_messages=model.input_messages,
        assistant_message=model.assistant_message,
        status=model.status,
        created_at=_ensure_aware(model.created_at),
        updated_at=_ensure_aware(model.updated_at),
    )


def _to_research_operation(
    model: ResearchStepModel,
) -> ResearchOperationRecord:
    observation = (
        ResearchObservation.model_validate(model.observation)
        if model.observation
        else None
    )
    return ResearchOperationRecord(
        step_id=model.step_id,
        task_id=model.task_id,
        decision_id=model.decision_id or "DECISION_LEGACY",
        turn_index=max(1, model.turn_index),
        operation_index=model.operation_index,
        tool_call_id=model.tool_call_id,
        tool_name=(
            model.tool_name
            or (observation.tool_name if observation is not None else "legacy")
        ),
        arguments=model.arguments or {},
        status=model.operation_status,
        result_payload=model.result_payload,
        observation=observation,
        uncertainty_reason=model.uncertainty_reason,
        created_at=_ensure_aware(model.created_at),
        updated_at=_ensure_aware(model.updated_at or model.created_at),
    )


def _to_event(model: WorkflowEventModel) -> WorkflowEvent:
    return WorkflowEvent(
        event_id=model.event_id,
        sequence=model.sequence,
        task_id=model.task_id,
        event_type=model.event_type,
        stage=model.stage,
        timestamp=_ensure_aware(model.timestamp),
        payload=model.payload,
    )


def _to_call_attempt(model: CallAttemptModel) -> CallAttempt:
    usage = ModelUsage.model_validate(model.usage) if model.usage is not None else None
    return CallAttempt(
        attempt_id=model.attempt_id,
        sequence=model.sequence,
        logical_call_id=model.logical_call_id,
        task_id=model.task_id,
        step_id=model.step_id,
        attempt_number=model.attempt_number,
        call_kind=model.call_kind,
        phase=model.phase,
        budget_phase=model.budget_phase,
        operation_name=model.operation_name,
        arguments_hash=model.arguments_hash,
        status=model.status,
        started_at=_ensure_aware(model.started_at),
        ended_at=(
            _ensure_aware(model.ended_at) if model.ended_at is not None else None
        ),
        error_type=model.error_type,
        result_ref=model.result_ref,
        usage=usage,
        reserved_prompt_tokens=model.reserved_prompt_tokens,
        reserved_completion_tokens=model.reserved_completion_tokens,
        duration_ms=model.duration_ms,
        created_at=_ensure_aware(model.created_at),
    )


def _operation_name(call_kind: str, payload: dict[str, object]) -> str:
    value = payload.get("operation_name") or payload.get("output_model")
    return str(value) if value is not None else call_kind


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _ensure_aware(value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _ensure_aware(parsed)
    return datetime.now(timezone.utc)


def _execution_from_task(model: ResearchTaskModel) -> ExecutionStats:
    payload = dict(model.execution_stats or {})
    payload["latency_ms"] = model.latency_ms or payload.get("latency_ms", 0)
    return ExecutionStats.model_validate(payload)
