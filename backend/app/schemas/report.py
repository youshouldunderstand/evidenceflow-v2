"""Structured claim and report schemas."""

from __future__ import annotations

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.schemas.common import (
    ClaimType,
    Identifier,
    NonEmptyStr,
    Uncertainty,
)


class ReportClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: Identifier
    text: NonEmptyStr
    dimension: NonEmptyStr
    claim_type: ClaimType
    requires_citation: bool
    evidence_ids: list[Identifier]
    uncertainty: Uncertainty

    @model_validator(mode="after")
    def validate_unique_evidence_ids(self) -> "ReportClaim":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique within a claim")
        return self


class ResearchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyStr
    executive_summary: NonEmptyStr
    claims: list[ReportClaim] = Field(min_length=1)
    comparison_table: list[dict[str, JsonValue]]
    recommendation: NonEmptyStr
    risks_and_limitations: list[NonEmptyStr]
    unverified_notice: str | None

    @field_validator("unverified_notice", mode="before")
    @classmethod
    def normalize_null_notice(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().casefold() == "null":
            return None
        return value

    @model_validator(mode="after")
    def validate_unique_claim_ids(self) -> "ResearchReport":
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim_id values must be unique")
        return self

