"""Reviewer semantic judgment schemas."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Identifier, NonEmptyStr, SourceType


class EvidenceSupportStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class ClaimSupportStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"


class ReportSection(StrEnum):
    EXECUTIVE_SUMMARY = "executive_summary"
    RECOMMENDATION = "recommendation"
    COMPARISON_TABLE = "comparison_table"


class ReportSectionStatus(StrEnum):
    SUPPORTED = "supported"
    CONTAINS_UNREVIEWED_FACT = "contains_unreviewed_fact"
    CHANGES_QUALIFICATION = "changes_qualification"


class EvidenceSupportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: Identifier
    evidence_id: Identifier
    status: EvidenceSupportStatus
    reason: NonEmptyStr
    assessment_method: Literal["semantic_model"] = "semantic_model"


class ClaimSupportResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: Identifier
    status: ClaimSupportStatus
    requires_citation_override: bool | None
    reason: NonEmptyStr
    assessment_method: Literal["semantic_model"] = "semantic_model"


class ReportSectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: ReportSection
    status: ReportSectionStatus
    related_claim_ids: list[Identifier]
    reason: NonEmptyStr
    assessment_method: Literal["semantic_model"] = "semantic_model"


class RequirementCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: Identifier
    covered: bool
    reason: NonEmptyStr


class GapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: Identifier
    question_ids: list[Identifier] = Field(default_factory=list)
    claim_ids: list[Identifier] = Field(default_factory=list)
    missing_information: NonEmptyStr
    suggested_source_types: list[SourceType] = Field(default_factory=list)
    reason: NonEmptyStr
    requires_research: bool

    @model_validator(mode="after")
    def validate_scope(self) -> "GapRequest":
        if not self.question_ids and not self.claim_ids:
            raise ValueError("GapRequest must reference a question or claim")
        if self.requires_research and not self.question_ids:
            raise ValueError("Research GapRequest must reference a question")
        return self


class ReviewConflict(BaseModel):
    """Reviewer 发现的冲突证据。

    仅靠 ``SupportType``（direct／partial）无法表达「相反」，因此冲突检测过去在
    数据模型上不可达。该结构让 Reviewer 报告它读出的冲突，由程序持久化到
    ``ResearchProgress.conflicts``，从而让「不选择性隐藏冲突」这条要求可被检查。
    """

    model_config = ConfigDict(extra="forbid")

    question_ids: list[Identifier] = Field(min_length=1)
    claim_ids: list[Identifier] = Field(default_factory=list)
    evidence_ids: list[Identifier] = Field(min_length=2)
    reason: NonEmptyStr

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ReviewConflict":
        if len(self.question_ids) != len(set(self.question_ids)):
            raise ValueError("conflict question_ids must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("conflict evidence_ids must be unique")
        return self


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unsupported_claims: list[Identifier]
    overclaimed_claims: list[Identifier]
    citation_issues: list[NonEmptyStr]
    missing_dimensions: list[NonEmptyStr]
    revision_instructions: list[NonEmptyStr]
    claim_support_results: list[ClaimSupportResult]
    evidence_support_results: list[EvidenceSupportResult]
    requirement_checks: list[RequirementCheck]
    report_section_results: list[ReportSectionResult] = Field(default_factory=list)
    gap_requests: list[GapRequest] = Field(default_factory=list)
    conflicts: list[ReviewConflict] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_review_keys(self) -> "ReviewResult":
        claim_ids = [item.claim_id for item in self.claim_support_results]
        evidence_pairs = [
            (item.claim_id, item.evidence_id)
            for item in self.evidence_support_results
        ]
        requirement_ids = [item.requirement_id for item in self.requirement_checks]
        sections = [item.section for item in self.report_section_results]
        gap_ids = [item.gap_id for item in self.gap_requests]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_support_results must contain unique claim_id values")
        if len(evidence_pairs) != len(set(evidence_pairs)):
            raise ValueError(
                "evidence_support_results must contain unique Claim-Evidence pairs"
            )
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement_checks must contain unique requirement_id values")
        if len(sections) != len(set(sections)):
            raise ValueError("report_section_results must contain unique sections")
        if len(gap_ids) != len(set(gap_ids)):
            raise ValueError("gap_requests must contain unique gap_id values")
        return self
