"""研究工作流的显式 LangGraph State 合同。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.eval import QualityMetrics
from app.schemas.report import ResearchReport
from app.schemas.research import ResearchPlan, SearchRecord
from app.schemas.progress import ResearchProgress
from app.schemas.review import ReviewResult


class ResearchState(TypedDict):
    task_id: str
    user_query: str
    plan: ResearchPlan | None
    searches: list[SearchRecord]
    sources: list[SourceRecord]
    evidence: list[EvidenceCard]
    draft_report: ResearchReport | None
    review: ReviewResult | None
    final_report: ResearchReport | None
    metrics: QualityMetrics | None
    current_stage: str
    revision_count: int
    citation_issues: list[dict[str, object]]
    citation_repair_count: int
    urls_redacted_count: int
    errors: list[str]
    research_stop_reason: str | None
    research_progress: ResearchProgress | None
    supplement_rounds: int
    attempted_gap_ids: list[str]
    model_calls: int
    tool_calls: int
    search_calls: int
    pages_read: int
    prompt_tokens: int
    completion_tokens: int
    actual_prompt_tokens: int
    actual_completion_tokens: int
    actual_model_attempts: int
    estimated_model_attempts: int
    unknown_model_attempts: int
    estimated_cost: float
    started_at: datetime
    updated_at: datetime


def create_initial_state(task_id: str, user_query: str) -> ResearchState:
    """创建完整初始 State，不依赖隐藏的可变全局值。"""

    now = datetime.now(timezone.utc)
    return ResearchState(
        task_id=task_id,
        user_query=user_query,
        plan=None,
        searches=[],
        sources=[],
        evidence=[],
        draft_report=None,
        review=None,
        final_report=None,
        metrics=None,
        current_stage="PENDING",
        revision_count=0,
        citation_issues=[],
        citation_repair_count=0,
        urls_redacted_count=0,
        errors=[],
        research_stop_reason=None,
        research_progress=None,
        supplement_rounds=0,
        attempted_gap_ids=[],
        model_calls=0,
        tool_calls=0,
        search_calls=0,
        pages_read=0,
        prompt_tokens=0,
        completion_tokens=0,
        actual_prompt_tokens=0,
        actual_completion_tokens=0,
        actual_model_attempts=0,
        estimated_model_attempts=0,
        unknown_model_attempts=0,
        estimated_cost=0.0,
        started_at=now,
        updated_at=now,
    )
