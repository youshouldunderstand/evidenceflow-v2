"""Deterministic evidence validation; no LLM decisions are made here."""

from __future__ import annotations

import re

from app.schemas.evidence import EvidenceCard, SourceRecord
from app.validators.results import ValidationIssue, ValidationResult


MIN_QUOTE_LENGTH = 20
MAX_QUOTE_LENGTH = 1000


def normalize_content(text: str) -> str:
    """Normalize Unicode whitespace without paraphrasing or changing words."""

    return re.sub(r"\s+", " ", text, flags=re.UNICODE).strip()


def validate_evidence(
    evidence: EvidenceCard,
    source: SourceRecord | None,
    known_urls: set[str],
    *,
    existing_evidence_ids: set[str] | None = None,
    known_question_ids: set[str] | None = None,
) -> ValidationResult:
    """Validate source identity, provenance, quote presence, and offsets."""

    issues: list[ValidationIssue] = []
    existing_ids = existing_evidence_ids or set()

    if evidence.evidence_id in existing_ids:
        issues.append(
            ValidationIssue(
                code="duplicate_evidence_id",
                message=f"Evidence ID already exists: {evidence.evidence_id}",
            )
        )
    if known_question_ids is not None:
        unknown_question_ids = [
            question_id
            for question_id in evidence.question_ids
            if question_id not in known_question_ids
        ]
        for question_id in unknown_question_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_question_id",
                    message=f"Unknown question ID: {question_id}",
                )
            )
    if source is None:
        issues.append(
            ValidationIssue(
                code="source_not_found",
                message=f"Source does not exist for evidence {evidence.evidence_id}",
            )
        )
        return ValidationResult.from_issues(issues)

    if evidence.source_id != source.source_id:
        issues.append(
            ValidationIssue(
                code="source_id_mismatch",
                message="Evidence source_id does not match the supplied source",
            )
        )
    if not source.url.strip():
        issues.append(ValidationIssue(code="empty_source_url", message="Source URL is empty"))
    if source.search_id is not None and source.url not in known_urls:
        issues.append(
            ValidationIssue(
                code="url_not_in_search_record",
                message=f"Source URL was not returned by a known search: {source.url}",
            )
        )
    if not source.raw_content.strip() or not source.normalized_content.strip():
        issues.append(
            ValidationIssue(
                code="empty_source_content", message="Source content is empty"
            )
        )

    quote_length = len(evidence.quote)
    if quote_length < MIN_QUOTE_LENGTH or quote_length > MAX_QUOTE_LENGTH:
        issues.append(
            ValidationIssue(
                code="invalid_quote_length",
                message=(
                    f"Quote length must be between {MIN_QUOTE_LENGTH} and "
                    f"{MAX_QUOTE_LENGTH} characters"
                ),
            )
        )
    if evidence.quote not in source.normalized_content:
        issues.append(
            ValidationIssue(
                code="quote_not_in_snapshot",
                message="Quote does not occur verbatim in normalized source content",
            )
        )

    if evidence.start_offset is not None and evidence.end_offset is not None:
        snapshot_slice = source.normalized_content[
            evidence.start_offset : evidence.end_offset
        ]
        if snapshot_slice != evidence.quote:
            issues.append(
                ValidationIssue(
                    code="quote_offset_mismatch",
                    message="Offsets do not select the supplied quote",
                )
            )

    return ValidationResult.from_issues(issues)
