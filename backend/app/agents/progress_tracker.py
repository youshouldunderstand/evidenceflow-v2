"""Deterministic updates for persisted research progress."""

from __future__ import annotations

from datetime import datetime, timezone

from app.reliability.runtime import ExecutionRuntime
from app.schemas.common import SupportType
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.progress import (
    BudgetRemaining,
    CoverageStatus,
    ProgressQuestion,
    QuestionCoverage,
    ResearchProgress,
)
from app.schemas.research import ResearchPlan


def create_research_progress(
    task_id: str,
    plan: ResearchPlan,
    runtime: ExecutionRuntime,
) -> ResearchProgress:
    questions = [
        ProgressQuestion(question_id=item.question_id, text=item.text)
        for item in plan.questions
    ]
    return ResearchProgress(
        task_id=task_id,
        questions=questions,
        coverage=[
            QuestionCoverage(question_id=item.question_id)
            for item in plan.questions
        ],
        open_gaps=[item.text for item in plan.questions],
        budget_remaining=budget_remaining(runtime),
        updated_at=datetime.now(timezone.utc),
    )


def apply_validated_evidence(
    progress: ResearchProgress,
    source: SourceRecord,
    evidence: list[EvidenceCard],
    runtime: ExecutionRuntime,
) -> list[QuestionCoverage]:
    """Update coverage using only deterministically validated evidence."""

    if source.source_id not in progress.source_refs:
        progress.source_refs.append(source.source_id)
    changed: list[QuestionCoverage] = []
    by_question = {item.question_id: item for item in progress.coverage}
    for item in evidence:
        if item.evidence_id not in progress.evidence_refs:
            progress.evidence_refs.append(item.evidence_id)
        for question_id in item.question_ids:
            coverage = by_question.get(question_id)
            if coverage is None:
                continue
            before = coverage.model_dump(mode="json")
            if item.evidence_id not in coverage.evidence_ids:
                coverage.evidence_ids.append(item.evidence_id)
            if item.support_type == SupportType.DIRECT:
                coverage.status = CoverageStatus.SUPPORTED
            elif coverage.status == CoverageStatus.UNANSWERED:
                coverage.status = CoverageStatus.PARTIAL
            if coverage.model_dump(mode="json") != before:
                changed.append(coverage.model_copy(deep=True))
    refresh_progress(progress, runtime)
    return changed


def refresh_progress(
    progress: ResearchProgress,
    runtime: ExecutionRuntime,
) -> None:
    question_text = {item.question_id: item.text for item in progress.questions}
    progress.open_gaps = [
        question_text[item.question_id]
        for item in progress.coverage
        if item.status != CoverageStatus.SUPPORTED
    ]
    progress.budget_remaining = budget_remaining(runtime)
    progress.stop_reason = runtime.research_stop_reason
    progress.updated_at = datetime.now(timezone.utc)


def budget_remaining(runtime: ExecutionRuntime) -> BudgetRemaining:
    usage = runtime.usage
    budget = runtime.budget
    consumed_tokens = usage.prompt_tokens + usage.completion_tokens
    return BudgetRemaining(
        total_model_attempts=usage.model_calls,
        research_model_attempts=runtime.research_model_calls,
        global_remaining_model_attempts=max(
            0, budget.max_model_calls - usage.model_calls
        ),
        research_remaining_model_attempts=max(
            0, budget.research_max_model_calls - runtime.research_model_calls
        ),
        downstream_reserved_model_attempts=budget.downstream_reserved_model_calls,
        global_remaining_tokens=max(0, budget.max_tokens - consumed_tokens),
        downstream_reserved_tokens=budget.downstream_reserved_tokens,
    )


def coverage_is_sufficient(progress: ResearchProgress) -> bool:
    return bool(progress.coverage) and all(
        item.status == CoverageStatus.SUPPORTED for item in progress.coverage
    )
