"""Deterministic structural validation of untrusted Reviewer output."""

from __future__ import annotations

from app.schemas.report import ResearchReport
from app.schemas.research import ResearchPlan
from app.schemas.review import ReportSection, ReviewResult
from app.validators.results import ValidationIssue, ValidationResult


def validate_review_result(
    plan: ResearchPlan, report: ResearchReport, review: ReviewResult
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    expected_claim_ids = {claim.claim_id for claim in report.claims}
    reviewed_claim_ids = {
        item.claim_id for item in review.claim_support_results
    }
    expected_pairs = {
        (claim.claim_id, evidence_id)
        for claim in report.claims
        for evidence_id in claim.evidence_ids
    }
    reviewed_pairs = {
        (item.claim_id, item.evidence_id)
        for item in review.evidence_support_results
    }
    expected_requirement_ids = {
        item.requirement_id for item in plan.requirements
    }
    reviewed_requirement_ids = {
        item.requirement_id for item in review.requirement_checks
    }
    expected_sections = set(ReportSection)
    reviewed_sections = {item.section for item in review.report_section_results}

    _compare_sets(
        issues,
        expected_claim_ids,
        reviewed_claim_ids,
        missing_code="missing_claim_review",
        unknown_code="unknown_claim_review",
        label="Claim review",
    )
    _compare_sets(
        issues,
        expected_pairs,
        reviewed_pairs,
        missing_code="missing_evidence_pair_review",
        unknown_code="unknown_evidence_pair_review",
        label="Claim-Evidence pair review",
    )
    _compare_sets(
        issues,
        expected_requirement_ids,
        reviewed_requirement_ids,
        missing_code="missing_requirement_check",
        unknown_code="unknown_requirement_check",
        label="Requirement check",
    )
    _compare_sets(
        issues,
        expected_sections,
        reviewed_sections,
        missing_code="missing_report_section_review",
        unknown_code="unknown_report_section_review",
        label="Report section review",
    )

    for section_result in review.report_section_results:
        unknown_claim_ids = set(section_result.related_claim_ids) - expected_claim_ids
        if unknown_claim_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_section_claim_reference",
                    message=(
                        f"Report section {section_result.section.value} references "
                        f"unknown Claim IDs: {sorted(unknown_claim_ids)}"
                    ),
                )
            )

    expected_question_ids = {item.question_id for item in plan.questions}
    for gap in review.gap_requests:
        unknown_claim_ids = set(gap.claim_ids) - expected_claim_ids
        unknown_question_ids = set(gap.question_ids) - expected_question_ids
        if unknown_claim_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_gap_claim",
                    message=(
                        f"Gap {gap.gap_id} references unknown Claim IDs: "
                        f"{sorted(unknown_claim_ids)}"
                    ),
                )
            )
        if unknown_question_ids:
            issues.append(
                ValidationIssue(
                    code="unknown_gap_question",
                    message=(
                        f"Gap {gap.gap_id} references unknown question IDs: "
                        f"{sorted(unknown_question_ids)}"
                    ),
                )
            )

    for label, identifiers in (
        ("unsupported_claims", set(review.unsupported_claims)),
        ("overclaimed_claims", set(review.overclaimed_claims)),
    ):
        unknown = identifiers - expected_claim_ids
        if unknown:
            issues.append(
                ValidationIssue(
                    code=f"unknown_{label}",
                    message=f"{label} contains unknown Claim IDs: {sorted(unknown)}",
                )
            )

    # 冲突条目可以引用**未被引用**的证据（这正是发现选择性引用的方式），
    # 因此这里只校验 question_ids / claim_ids；evidence_ids 交由报告侧校验。
    for conflict in review.conflicts:
        unknown_questions = set(conflict.question_ids) - expected_question_ids
        unknown_claims = set(conflict.claim_ids) - expected_claim_ids
        if unknown_questions:
            issues.append(
                ValidationIssue(
                    code="unknown_conflict_question",
                    message=(
                        f"Conflict references unknown question IDs: "
                        f"{sorted(unknown_questions)}"
                    ),
                )
            )
        if unknown_claims:
            issues.append(
                ValidationIssue(
                    code="unknown_conflict_claim",
                    message=(
                        f"Conflict references unknown Claim IDs: "
                        f"{sorted(unknown_claims)}"
                    ),
                )
            )

    return ValidationResult.from_issues(issues)


def _compare_sets(
    issues: list[ValidationIssue],
    expected: set[object],
    actual: set[object],
    *,
    missing_code: str,
    unknown_code: str,
    label: str,
) -> None:
    missing = expected - actual
    unknown = actual - expected
    if missing:
        issues.append(
            ValidationIssue(
                code=missing_code,
                message=f"{label} is missing entries: {sorted(missing)}",
            )
        )
    if unknown:
        issues.append(
            ValidationIssue(
                code=unknown_code,
                message=f"{label} contains unknown entries: {sorted(unknown)}",
            )
        )
