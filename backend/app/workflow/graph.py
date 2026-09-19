"""EvidenceFlow 工作流：证据闭环、审核、恢复与真实执行事件。"""

from __future__ import annotations

import time
import json
from collections.abc import Awaitable, Callable
from typing import Literal, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.manager import ManagerAgent
from app.agents.contracts import ResearchCollection
from app.agents.researcher import ResearcherAgent
from app.agents.reviewer import ReviewerAgent
from app.persistence.repositories import ResearchRepository
from app.reliability.exceptions import (
    CitationValidationFailure,
    EvidenceValidationFailure,
    ReviewValidationFailure,
)
from app.reliability.runtime import (
    ExecutionRuntime,
    FailureInjector,
    RetryPolicy,
    duration_ms,
)
from app.schemas.common import TaskStatus
from app.schemas.event import WorkflowEventType
from app.schemas.progress import (
    EvidenceConflict,
    ResearchObservation,
    ResearchProgress,
)
from app.schemas.report import ResearchReport
from app.schemas.review import ReviewResult
from app.schemas.usage import CallKind, UsageMeasurement
from app.validators.citation_validator import (
    redact_report_urls,
    validate_citations,
)
from app.validators.delivery_self_check import run_delivery_self_check
from app.validators.metrics import calculate_metrics
from app.validators.results import ValidationResult
from app.validators.review_validator import validate_review_result
from app.workflow.state import ResearchState, create_initial_state
from app.workflow.budget import WorkflowBudget, WorkflowUsage


MAX_REVISION = 1
# 引文定向修复最多一次：报告侧格式问题允许一次重写，但不得无限循环。
MAX_CITATION_REPAIR = 1
UNVERIFIED_NOTICE = (
    "当前报告仍存在部分未完全验证的结论，请结合原始来源谨慎参考。"
)


class EvidenceFlowWorkflow:
    def __init__(
        self,
        manager: ManagerAgent,
        researcher: ResearcherAgent,
        reviewer: ReviewerAgent,
        repository: ResearchRepository,
        *,
        workflow_version: str,
        budget: WorkflowBudget | None = None,
        retry_policy: RetryPolicy | None = None,
        failure_injector: FailureInjector | None = None,
        model_output_reserve_tokens: int = 4096,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self._manager = manager
        self._researcher = researcher
        self._reviewer = reviewer
        self._repository = repository
        self._workflow_version = workflow_version
        self._budget = budget or WorkflowBudget()
        self._retry_policy = retry_policy or RetryPolicy()
        self._failure_injector = failure_injector or FailureInjector()
        self._model_output_reserve_tokens = model_output_reserve_tokens
        self._checkpointer = checkpointer or InMemorySaver()
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(ResearchState)
        builder.add_node(
            "manager_plan", self._observed_node("manager_plan", self._manager_plan)
        )
        builder.add_node(
            "researcher_collect",
            self._observed_node("researcher_collect", self._researcher_collect),
        )
        builder.add_node(
            "manager_write", self._observed_node("manager_write", self._manager_write)
        )
        builder.add_node(
            "validate_citations",
            self._observed_node("validate_citations", self._validate_citations),
        )
        builder.add_node(
            "reviewer_check",
            self._observed_node("reviewer_check", self._reviewer_check),
        )
        builder.add_node(
            "calculate_metrics",
            self._observed_node("calculate_metrics", self._calculate_metrics),
        )
        builder.add_node(
            "supplement_research",
            self._observed_node(
                "supplement_research", self._supplement_research
            ),
        )
        builder.add_node(
            "manager_revise",
            self._observed_node("manager_revise", self._manager_revise),
        )
        builder.add_node(
            "manager_repair_citations",
            self._observed_node(
                "manager_repair_citations", self._manager_repair_citations
            ),
        )
        builder.add_node(
            "mark_unverified",
            self._observed_node("mark_unverified", self._mark_unverified),
        )
        builder.add_node(
            "save_report", self._observed_node("save_report", self._save_report)
        )

        builder.add_edge(START, "manager_plan")
        builder.add_edge("manager_plan", "researcher_collect")
        builder.add_edge("researcher_collect", "manager_write")
        builder.add_edge("manager_write", "validate_citations")
        builder.add_conditional_edges(
            "validate_citations",
            self._route_after_citation_validation,
            {
                "reviewer_check": "reviewer_check",
                "manager_repair_citations": "manager_repair_citations",
            },
        )
        builder.add_edge("manager_repair_citations", "validate_citations")
        builder.add_conditional_edges(
            "reviewer_check",
            self._route_after_review,
            {
                "calculate_metrics": "calculate_metrics",
                "supplement_research": "supplement_research",
            },
        )
        builder.add_edge("supplement_research", "manager_revise")
        builder.add_conditional_edges(
            "calculate_metrics",
            self._route_after_metrics,
            {
                "save_report": "save_report",
                "manager_revise": "manager_revise",
                "mark_unverified": "mark_unverified",
            },
        )
        builder.add_edge("manager_revise", "validate_citations")
        builder.add_edge("mark_unverified", "save_report")
        builder.add_edge("save_report", END)
        return builder.compile(checkpointer=self._checkpointer)

    async def run(self, task_id: str, user_query: str) -> ResearchState:
        config = {"configurable": {"thread_id": task_id}}
        snapshot = await self._graph.aget_state(config)
        graph_input: ResearchState | None
        if snapshot.values and snapshot.next:
            self._repository.append_event(
                task_id,
                WorkflowEventType.WORKFLOW_RESUMED,
                stage=str(snapshot.values.get("current_stage", "UNKNOWN")),
                payload={"next_nodes": list(snapshot.next)},
            )
            graph_input = None
        elif snapshot.values:
            # The graph reached END. A crash can still happen while the service
            # publishes the terminal task status; reuse the committed graph result.
            return cast(ResearchState, snapshot.values)
        else:
            graph_input = create_initial_state(task_id, user_query)
        result = await self._graph.ainvoke(graph_input, config=config)
        return cast(ResearchState, result)

    async def _manager_plan(self, state: ResearchState) -> dict[str, object]:
        self._repository.update_status(state["task_id"], TaskStatus.PLANNING)
        runtime = self._runtime(state)
        plan = await self._manager.plan(state["user_query"], runtime)
        self._repository.save_plan(state["task_id"], plan)
        return {
            "plan": plan,
            "current_stage": TaskStatus.PLANNING.value,
            **runtime.state_usage(),
        }

    async def _researcher_collect(self, state: ResearchState) -> dict[str, object]:
        if state["plan"] is None:
            raise EvidenceValidationFailure("ResearchPlan is missing")
        self._repository.update_status(state["task_id"], TaskStatus.RESEARCHING)
        runtime = self._runtime(state, phase="research")
        collection = await self._researcher.collect(
            state["task_id"],
            state["plan"],
            runtime,
            checkpoint=self._save_research_checkpoint,
            prior_collection=self._repository.get_collection(state["task_id"]),
            recovery_store=self._repository,
        )
        self._repository.update_status(
            state["task_id"], TaskStatus.VALIDATING_EVIDENCE
        )
        self._repository.save_collection(collection)
        for source in collection.sources:
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.SOURCE_FETCHED,
                stage=TaskStatus.VALIDATING_EVIDENCE.value,
                payload={
                    "source_id": source.source_id,
                    "title": source.title,
                    "url": source.url,
                },
            )
        for evidence in collection.evidence:
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.EVIDENCE_CREATED,
                stage=TaskStatus.VALIDATING_EVIDENCE.value,
                payload={
                    "evidence_id": evidence.evidence_id,
                    "source_id": evidence.source_id,
                    "question_id": evidence.question_id,
                    "support_type": evidence.support_type.value,
                },
            )
        for rejected in collection.rejected_evidence:
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.EVIDENCE_REJECTED,
                stage=TaskStatus.VALIDATING_EVIDENCE.value,
                payload={
                    "evidence_id": rejected.evidence_id,
                    "source_id": rejected.source_id,
                    "reasons": rejected.reasons,
                },
            )
        if collection.stop_reason is not None:
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.RESEARCH_STOPPED,
                stage=TaskStatus.VALIDATING_EVIDENCE.value,
                payload={"reason": collection.stop_reason},
            )
        if not collection.evidence:
            raise EvidenceValidationFailure(
                "No EvidenceCard passed deterministic validation"
            )
        return {
            "searches": collection.searches,
            "sources": collection.sources,
            "evidence": collection.evidence,
            "errors": state["errors"]
            + collection.errors
            + [
                f"Evidence {item.evidence_id} rejected: {'; '.join(item.reasons)}"
                for item in collection.rejected_evidence
            ],
            "research_stop_reason": collection.stop_reason,
            "research_progress": collection.progress,
            "current_stage": TaskStatus.VALIDATING_EVIDENCE.value,
            **runtime.state_usage(),
        }

    async def _manager_write(self, state: ResearchState) -> dict[str, object]:
        if state["plan"] is None:
            raise CitationValidationFailure("ResearchPlan is missing")
        self._repository.update_status(state["task_id"], TaskStatus.WRITING)
        runtime = self._runtime(state)
        report = await self._manager.write(
            state["user_query"],
            state["plan"],
            state["evidence"],
            runtime,
            progress=state["research_progress"],
            sources=state["sources"],
        )
        progress = state["research_progress"]
        if progress is not None and progress.open_gaps:
            gap_ids = [
                item.question_id
                for item in progress.coverage
                if item.status != "supported"
            ]
            report = report.model_copy(
                update={
                    "unverified_notice": (
                        "研究停止时仍有未覆盖问题：" + ", ".join(gap_ids)
                    )
                }
            )
        return {
            "draft_report": report,
            "citation_issues": [],
            "current_stage": TaskStatus.WRITING.value,
            **runtime.state_usage(),
        }

    async def _validate_citations(self, state: ResearchState) -> dict[str, object]:
        report = state["draft_report"]
        if report is None:
            raise CitationValidationFailure("Draft report is missing")
        self._repository.update_status(
            state["task_id"], TaskStatus.VALIDATING_CITATIONS
        )

        # 兜底层：确定性剔除报告正文中的链接。最终引用 URL 只由持久化
        # SourceRecord 渲染，正文里的链接没有信息增益，因此剔除不丢内容；
        # 但必须记录剔除次数，不能静默处理。
        sanitized, redacted = redact_report_urls(report)
        if redacted:
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.REPORT_URLS_REDACTED,
                stage=TaskStatus.VALIDATING_CITATIONS.value,
                payload={"redacted_count": redacted},
            )
        accumulated_redacted = state.get("urls_redacted_count", 0) + redacted

        evidence_by_id = {item.evidence_id: item for item in state["evidence"]}
        sources_by_id = {item.source_id: item for item in state["sources"]}
        result = validate_citations(sanitized, evidence_by_id, sources_by_id)

        if result.valid:
            return {
                "draft_report": sanitized,
                "citation_issues": [],
                "urls_redacted_count": accumulated_redacted,
                "current_stage": TaskStatus.VALIDATING_CITATIONS.value,
            }

        # 可修复问题（报告侧、仅凭同一批证据重写即可修好）允许一次有界定向修复。
        if (
            result.only_repairable
            and state.get("citation_repair_count", 0) < MAX_CITATION_REPAIR
            and self._citation_repair_budget_available(state)
        ):
            return {
                "draft_report": sanitized,
                "citation_issues": [
                    item.model_dump(mode="json") for item in result.repairable_issues
                ],
                "urls_redacted_count": accumulated_redacted,
                "current_stage": TaskStatus.VALIDATING_CITATIONS.value,
            }

        # 含不可修复问题（证据链损坏），或修复预算已用尽。
        self._persist_rejected_draft(state, sanitized, result)
        messages = "; ".join(issue.message for issue in result.issues)
        raise CitationValidationFailure(messages)

    def _citation_repair_budget_available(self, state: ResearchState) -> bool:
        """修复消耗一次模型调用，且必须为其后的审核留出额度。"""

        remaining_model_calls = self._budget.max_model_calls - state["model_calls"]
        consumed_tokens = state["prompt_tokens"] + state["completion_tokens"]
        remaining_tokens = self._budget.max_tokens - consumed_tokens
        return (
            remaining_model_calls >= 2
            and remaining_tokens >= 2 * self._model_output_reserve_tokens
        )

    def _persist_rejected_draft(
        self,
        state: ResearchState,
        report: ResearchReport,
        result: ValidationResult,
    ) -> None:
        """落盘被拒草稿与问题清单，避免失败后无法定位原因。"""

        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.REPORT_VALIDATION_FAILED,
            stage=TaskStatus.VALIDATING_CITATIONS.value,
            payload={
                "issues": [item.model_dump(mode="json") for item in result.issues],
                "fatal_codes": sorted({item.code for item in result.fatal_issues}),
                "repairable_codes": sorted(
                    {item.code for item in result.repairable_issues}
                ),
                "citation_repair_count": state.get("citation_repair_count", 0),
                "rejected_report": report.model_dump(mode="json"),
            },
        )

    def _route_after_citation_validation(
        self, state: ResearchState
    ) -> Literal["reviewer_check", "manager_repair_citations"]:
        if state.get("citation_issues"):
            return "manager_repair_citations"
        return "reviewer_check"

    async def _manager_repair_citations(
        self, state: ResearchState
    ) -> dict[str, object]:
        plan = state["plan"]
        report = state["draft_report"]
        issues = state.get("citation_issues") or []
        if plan is None or report is None or not issues:
            raise CitationValidationFailure("Citation repair context is incomplete")
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.CITATION_REPAIR_STARTED,
            stage=TaskStatus.VALIDATING_CITATIONS.value,
            payload={
                "issue_codes": sorted({str(item.get("code")) for item in issues})
            },
        )
        runtime = self._runtime(state)
        repaired = await self._manager.repair_citations(
            state["user_query"],
            plan,
            state["evidence"],
            report,
            issues,
            runtime,
            sources=state["sources"],
        )
        repair_count = state.get("citation_repair_count", 0) + 1
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.CITATION_REPAIR_COMPLETED,
            stage=TaskStatus.VALIDATING_CITATIONS.value,
            payload={"repair_count": repair_count},
        )
        return {
            "draft_report": repaired,
            "citation_issues": [],
            "citation_repair_count": repair_count,
            "current_stage": TaskStatus.VALIDATING_CITATIONS.value,
            **runtime.state_usage(),
        }

    async def _reviewer_check(self, state: ResearchState) -> dict[str, object]:
        if state["plan"] is None or state["draft_report"] is None:
            raise CitationValidationFailure("Reviewer context is incomplete")
        self._repository.update_status(state["task_id"], TaskStatus.REVIEWING)
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.REVIEW_STARTED,
            stage=TaskStatus.REVIEWING.value,
            payload={"revision_count": state["revision_count"]},
        )
        review_state = {**state, "current_stage": TaskStatus.REVIEWING.value}
        runtime = self._runtime(review_state)
        review = await self._reviewer.review(
            state["plan"],
            state["draft_report"],
            state["evidence"],
            state["sources"],
            runtime,
            progress=state["research_progress"],
        )
        validation = validate_review_result(
            state["plan"], state["draft_report"], review
        )
        if not validation.valid:
            messages = "; ".join(issue.message for issue in validation.issues)
            raise ReviewValidationFailure(messages)
        self._record_review_conflicts(state, review)
        return {
            "review": review,
            "current_stage": TaskStatus.REVIEWING.value,
            **runtime.state_usage(),
        }

    def _record_review_conflicts(
        self, state: ResearchState, review: ReviewResult
    ) -> None:
        """把 Reviewer 报告的冲突落盘到 ResearchProgress。

        仅靠 ``SupportType`` 无法表达「相反」，冲突检测过去在数据模型上不可达。
        Reviewer 是当前唯一能读出冲突的角色，因此由它报告、由程序持久化，使
        「不选择性隐藏冲突」成为可检查的事实。此处在审核之后发生，不影响研究
        阶段的预算与停止决策。
        """

        progress = state["research_progress"]
        if progress is None or not review.conflicts:
            return
        known = {
            (item.question_id, tuple(sorted(item.evidence_ids)))
            for item in progress.conflicts
        }
        added = 0
        for conflict in review.conflicts:
            for question_id in conflict.question_ids:
                key = (question_id, tuple(sorted(conflict.evidence_ids)))
                if key in known:
                    continue
                known.add(key)
                progress.conflicts.append(
                    EvidenceConflict(
                        question_id=question_id,
                        evidence_ids=list(conflict.evidence_ids),
                        reason=conflict.reason,
                    )
                )
                added += 1
        if not added:
            return
        self._repository.save_research_progress(progress)
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.CONFLICTS_IDENTIFIED,
            stage=TaskStatus.REVIEWING.value,
            payload={
                "conflict_count": len(progress.conflicts),
                "new_conflicts": added,
                "question_ids": sorted(
                    {item.question_id for item in progress.conflicts}
                ),
            },
        )

    async def _calculate_metrics(self, state: ResearchState) -> dict[str, object]:
        if (
            state["plan"] is None
            or state["draft_report"] is None
            or state["review"] is None
        ):
            raise CitationValidationFailure("Metrics context is incomplete")
        metrics = calculate_metrics(
            state["plan"],
            state["draft_report"],
            state["review"],
            {item.evidence_id: item for item in state["evidence"]},
        )
        self._repository.append_event(
            state["task_id"],
            (
                WorkflowEventType.REVIEW_PASSED
                if metrics.passed
                else WorkflowEventType.REVIEW_FAILED
            ),
            stage=TaskStatus.REVIEWING.value,
            payload={
                "passed": metrics.passed,
                "overall_score": metrics.overall_score,
                "revision_count": state["revision_count"],
            },
        )
        return {"metrics": metrics}

    def _route_after_review(
        self, state: ResearchState
    ) -> Literal["calculate_metrics", "supplement_research"]:
        review = state["review"]
        if review is None:
            raise CitationValidationFailure("Review is missing")
        pending_gaps = [
            item
            for item in review.gap_requests
            if item.requires_research
            and item.gap_id not in state.get("attempted_gap_ids", [])
        ]
        if (
            not pending_gaps
            or state.get("supplement_rounds", 0)
            >= self._budget.max_supplement_rounds
        ):
            return "calculate_metrics"
        remaining_model_calls = self._budget.max_model_calls - state["model_calls"]
        consumed_tokens = state["prompt_tokens"] + state["completion_tokens"]
        remaining_tokens = self._budget.max_tokens - consumed_tokens
        if (
            remaining_model_calls
            <= self._budget.post_supplement_reserved_model_calls
            or remaining_tokens
            <= self._budget.post_supplement_reserved_tokens
            + self._model_output_reserve_tokens
        ):
            return "calculate_metrics"
        return "supplement_research"

    async def _supplement_research(
        self, state: ResearchState
    ) -> dict[str, object]:
        if state["plan"] is None or state["review"] is None:
            raise EvidenceValidationFailure("Supplement context is incomplete")
        gaps = [
            item
            for item in state["review"].gap_requests
            if item.requires_research
            and item.gap_id not in state.get("attempted_gap_ids", [])
        ]
        gap_ids = [item.gap_id for item in gaps]
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.SUPPLEMENT_STARTED,
            stage=TaskStatus.RESEARCHING.value,
            payload={"gap_ids": gap_ids},
        )
        prior = ResearchCollection(
            searches=state["searches"],
            sources=state["sources"],
            evidence=state["evidence"],
            rejected_evidence=[],
            errors=state["errors"],
            model_calls=0,
            tool_calls=0,
            search_calls=0,
            pages_read=0,
            progress=state["research_progress"],
        )
        runtime = self._runtime(state, phase="supplement")
        collection = await self._researcher.collect(
            state["task_id"],
            state["plan"],
            runtime,
            checkpoint=self._save_research_checkpoint,
            prior_collection=prior,
            research_directive=json.dumps(
                [item.model_dump(mode="json") for item in gaps],
                ensure_ascii=False,
            ),
            recovery_store=self._repository,
        )
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.SUPPLEMENT_COMPLETED,
            stage=TaskStatus.VALIDATING_EVIDENCE.value,
            payload={
                "gap_ids": gap_ids,
                "evidence_count": len(collection.evidence),
                "stop_reason": collection.stop_reason,
            },
        )
        return {
            "searches": collection.searches,
            "sources": collection.sources,
            "evidence": collection.evidence,
            "errors": collection.errors,
            "research_progress": collection.progress,
            "research_stop_reason": collection.stop_reason,
            "supplement_rounds": state.get("supplement_rounds", 0) + 1,
            "attempted_gap_ids": state.get("attempted_gap_ids", []) + gap_ids,
            "current_stage": TaskStatus.VALIDATING_EVIDENCE.value,
            **runtime.state_usage(),
        }

    def _route_after_metrics(
        self, state: ResearchState
    ) -> Literal["save_report", "manager_revise", "mark_unverified"]:
        metrics = state["metrics"]
        if metrics is None:
            raise CitationValidationFailure("Metrics are missing")
        if metrics.passed:
            return "save_report"
        remaining_model_calls = self._budget.max_model_calls - state["model_calls"]
        consumed_tokens = state["prompt_tokens"] + state["completion_tokens"]
        remaining_tokens = self._budget.max_tokens - consumed_tokens
        if (
            state["revision_count"] < MAX_REVISION
            and remaining_model_calls >= 2
            and remaining_tokens >= 2 * self._model_output_reserve_tokens
        ):
            return "manager_revise"
        return "mark_unverified"

    async def _manager_revise(self, state: ResearchState) -> dict[str, object]:
        if (
            state["plan"] is None
            or state["draft_report"] is None
            or state["review"] is None
        ):
            raise CitationValidationFailure("Revision context is incomplete")
        self._repository.update_status(state["task_id"], TaskStatus.REVISING)
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.REVISION_STARTED,
            stage=TaskStatus.REVISING.value,
            payload={"revision_count": state["revision_count"] + 1},
        )
        runtime = self._runtime(state)
        revised_report = await self._manager.revise(
            state["user_query"],
            state["plan"],
            state["evidence"],
            state["draft_report"],
            state["review"],
            runtime,
            sources=state["sources"],
        )
        result = {
            "draft_report": revised_report,
            "review": None,
            "metrics": None,
            "citation_issues": [],
            "revision_count": state["revision_count"] + 1,
            "current_stage": TaskStatus.REVISING.value,
            **runtime.state_usage(),
        }
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.REVISION_COMPLETED,
            stage=TaskStatus.REVISING.value,
            payload={"revision_count": state["revision_count"] + 1},
        )
        return result

    async def _mark_unverified(self, state: ResearchState) -> dict[str, object]:
        report = state["draft_report"]
        if report is None:
            raise CitationValidationFailure("Draft report is missing")
        marked_report = report.model_copy(
            update={
                "unverified_notice": " ".join(
                    dict.fromkeys(
                        filter(
                            None,
                            [report.unverified_notice, UNVERIFIED_NOTICE],
                        )
                    )
                )
            }
        )
        return {
            "draft_report": marked_report,
            "final_report": marked_report,
            "current_stage": TaskStatus.COMPLETED_WITH_WARNINGS.value,
        }

    async def _save_report(self, state: ResearchState) -> dict[str, object]:
        report = state["final_report"] or state["draft_report"]
        review = state["review"]
        metrics = state["metrics"]
        if report is None or review is None or metrics is None:
            raise CitationValidationFailure("Final report context is incomplete")

        self_check = run_delivery_self_check(
            report, state["evidence"], state["sources"]
        )
        self._repository.append_event(
            state["task_id"],
            WorkflowEventType.DELIVERY_SELF_CHECK_COMPLETED,
            stage=state["current_stage"],
            payload={
                "status": self_check.status,
                "deterministic_only": self_check.deterministic_only,
                "checked_evidence": self_check.checked_evidence,
                "checked_sources": self_check.checked_sources,
                "finding_codes": [item.code.value for item in self_check.findings],
                "blocking_count": len(self_check.blocking_findings),
                "findings": [
                    item.model_dump(mode="json") for item in self_check.findings
                ],
            },
        )
        if self_check.findings:
            # 只标注，不改交付状态、不删内容：报告照常落盘，问题由 notice 与
            # DELIVERY_SELF_CHECK_COMPLETED 事件显式暴露，不静默。
            existing_notice = report.unverified_notice
            self_check_notice = self_check.notice
            report = report.model_copy(
                update={
                    "unverified_notice": (
                        f"{existing_notice} {self_check_notice}"
                        if existing_notice
                        else self_check_notice
                    )
                }
            )

        progress = state["research_progress"]
        conflicts = list(progress.conflicts) if progress is not None else []
        if conflicts:
            # 冲突未能消解时不允许静默通过：显式告知用户，并强制降级为警告交付。
            question_ids = ", ".join(
                sorted({item.question_id for item in conflicts})
            )
            conflict_notice = (
                "研究过程中发现相互冲突的证据且未完全消解："
                f"{question_ids}。相关结论可能不适用于所有条件。"
            )
            existing = report.unverified_notice
            report = report.model_copy(
                update={
                    "unverified_notice": (
                        f"{existing} {conflict_notice}"
                        if existing
                        else conflict_notice
                    )
                }
            )
        status = (
            TaskStatus.COMPLETED
            if metrics.passed
            and not conflicts
            and not (progress is not None and progress.open_gaps)
            else TaskStatus.COMPLETED_WITH_WARNINGS
        )
        self._repository.save_report(
            task_id=state["task_id"],
            workflow_version=self._workflow_version,
            report=report,
            review=review,
            metrics=metrics,
            revision_count=state["revision_count"],
        )
        return {"final_report": report, "current_stage": status.value}

    def _save_research_checkpoint(
        self,
        progress: ResearchProgress,
        observation: ResearchObservation | None,
        collection: ResearchCollection,
    ) -> None:
        self._repository.save_collection(collection)
        if observation is None:
            self._repository.save_research_progress(progress)
        else:
            self._repository.record_research_observation(progress, observation)
            self._repository.append_event(
                progress.task_id,
                WorkflowEventType.RESEARCH_PROGRESS_UPDATED,
                stage=TaskStatus.RESEARCHING.value,
                payload={
                    "step_id": observation.step_id,
                    "tool_call_id": observation.tool_call_id,
                    "candidate_urls": observation.candidate_urls,
                    "new_candidate_urls": observation.new_candidate_urls,
                    "step_index": observation.step_index,
                    "coverage_changes": [
                        item.model_dump(mode="json")
                        for item in observation.coverage_changes
                    ],
                    "open_gap_count": len(progress.open_gaps),
                    "stop_reason": progress.stop_reason,
                },
            )

    def _runtime(
        self, state: ResearchState, *, phase: str = "general"
    ) -> ExecutionRuntime:
        """从可持久化 State 重建当前节点的调用控制器。"""

        attempts = self._repository.get_call_attempts(state["task_id"])
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

        def charged_tokens(kind: Literal["prompt", "completion"]) -> int:
            total = 0
            for item in model_attempts:
                actual = (
                    getattr(item.usage, f"{kind}_tokens")
                    if item.usage is not None
                    and item.usage.measurement == UsageMeasurement.ACTUAL
                    else None
                )
                reserved = getattr(item, f"reserved_{kind}_tokens")
                total += actual if actual is not None else (reserved or 0)
            return total

        usage = WorkflowUsage(
            model_calls=max(state["model_calls"], len(model_attempts)),
            tool_calls=max(
                state["tool_calls"],
                sum(
                    item.call_kind in {CallKind.SEARCH, CallKind.PAGE}
                    for item in attempts
                ),
            ),
            search_calls=max(
                state["search_calls"],
                sum(item.call_kind == CallKind.SEARCH for item in attempts),
            ),
            pages_read=max(
                state["pages_read"],
                sum(item.call_kind == CallKind.PAGE for item in attempts),
            ),
            prompt_tokens=max(state["prompt_tokens"], charged_tokens("prompt")),
            completion_tokens=max(
                state["completion_tokens"], charged_tokens("completion")
            ),
            actual_prompt_tokens=max(
                state.get("actual_prompt_tokens", 0),
                sum(
                    item.usage.prompt_tokens or 0
                    for item in actual_attempts
                    if item.usage is not None
                ),
            ),
            actual_completion_tokens=max(
                state.get("actual_completion_tokens", 0),
                sum(
                    item.usage.completion_tokens or 0
                    for item in actual_attempts
                    if item.usage is not None
                ),
            ),
            actual_model_attempts=max(
                state.get("actual_model_attempts", 0), len(actual_attempts)
            ),
            estimated_model_attempts=max(
                state.get("estimated_model_attempts", 0), len(estimated_attempts)
            ),
            unknown_model_attempts=max(
                state.get("unknown_model_attempts", 0), len(unknown_attempts)
            ),
            estimated_cost=state["estimated_cost"],
        )
        return ExecutionRuntime(
            self._budget,
            usage,
            retry_policy=self._retry_policy,
            failure_injector=self._failure_injector,
            retry_observer=lambda kind, attempt, error, delay: self._record_retry(
                state, kind, attempt, error, delay
            ),
            call_observer=lambda phase, kind, attempt, payload: self._record_call(
                state, phase, kind, attempt, payload
            ),
            model_output_reserve_tokens=self._model_output_reserve_tokens,
            phase=phase,
            phase_model_calls=sum(
                item.call_kind == CallKind.MODEL
                and item.budget_phase == phase
                for item in attempts
            ),
        )

    def _observed_node(
        self,
        node_name: str,
        operation: Callable[[ResearchState], Awaitable[dict[str, object]]],
    ) -> Callable[[ResearchState], Awaitable[dict[str, object]]]:
        """包装节点并记录与真实执行一致的开始、完成和失败事件。"""

        async def observed(state: ResearchState) -> dict[str, object]:
            started_at = time.perf_counter()
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.NODE_STARTED,
                stage=state["current_stage"],
                payload={"node": node_name},
            )
            try:
                result = await operation(state)
            except Exception as exc:
                self._repository.append_event(
                    state["task_id"],
                    WorkflowEventType.NODE_FAILED,
                    stage=state["current_stage"],
                    payload={
                        "node": node_name,
                        "error_type": type(exc).__name__,
                        "duration_ms": duration_ms(started_at),
                    },
                )
                raise
            self._repository.append_event(
                state["task_id"],
                WorkflowEventType.NODE_COMPLETED,
                stage=str(result.get("current_stage", state["current_stage"])),
                payload={
                    "node": node_name,
                    "duration_ms": duration_ms(started_at),
                },
            )
            return result

        return observed

    def _record_retry(
        self,
        state: ResearchState,
        kind: str,
        attempt: int,
        error: Exception,
        delay: float,
    ) -> None:
        self._repository.append_event(
            state["task_id"],
            (
                WorkflowEventType.MODEL_CALL_RETRIED
                if kind == "model"
                else WorkflowEventType.TOOL_CALL_RETRIED
            ),
            stage=state["current_stage"],
            payload={
                "call_kind": kind,
                "attempt": attempt,
                "error_type": type(error).__name__,
                "delay_seconds": round(delay, 6),
            },
        )

    def _record_call(
        self,
        state: ResearchState,
        phase: str,
        kind: str,
        attempt: int,
        payload: dict[str, object],
    ) -> None:
        self._repository.record_call_attempt(
            state["task_id"],
            workflow_phase=state["current_stage"],
            call_kind=kind,
            attempt_number=attempt,
            transition=phase,
            payload=payload,
        )
        if kind == "model":
            event_type = {
                "started": WorkflowEventType.MODEL_CALL_STARTED,
                "completed": WorkflowEventType.MODEL_CALL_COMPLETED,
                "failed": WorkflowEventType.MODEL_CALL_FAILED,
            }[phase]
        else:
            event_type = {
                "started": WorkflowEventType.TOOL_CALL_STARTED,
                "completed": WorkflowEventType.TOOL_CALL_COMPLETED,
                "failed": WorkflowEventType.TOOL_CALL_FAILED,
            }[phase]
        self._repository.append_event(
            state["task_id"],
            event_type,
            stage=state["current_stage"],
            payload={"call_kind": kind, "attempt": attempt, **payload},
        )
