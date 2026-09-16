"""Phase 2 API response showing the complete deterministic citation chain."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Identifier, NonEmptyStr, TaskStatus
from app.schemas.eval import ExecutionStats, QualityMetrics
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.report import ResearchReport
from app.schemas.review import ReviewResult
from app.validators.delivery_self_check import DeliverySelfCheck


class CitationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: Identifier
    evidence_id: Identifier
    source_id: Identifier
    source_title: NonEmptyStr
    quote: NonEmptyStr
    source_url: NonEmptyStr


class ResearchReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    status: TaskStatus
    report: ResearchReport
    evidence: list[EvidenceCard]
    sources: list[SourceRecord]
    citations: list[CitationView]
    review: ReviewResult
    metrics: QualityMetrics
    # 交付前确定性自检结论（零模型调用）。按需重算，始终与当前持久化数据一致。
    delivery_self_check: DeliverySelfCheck | None = None
    execution: ExecutionStats = Field(default_factory=ExecutionStats)
    provider_notice: NonEmptyStr
