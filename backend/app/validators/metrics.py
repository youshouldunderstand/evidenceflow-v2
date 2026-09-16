"""Deterministic report metrics and final pass/fail decision."""

from __future__ import annotations

from app.schemas.eval import QualityMetrics
from app.schemas.evidence import EvidenceCard
from app.schemas.report import ResearchReport
from app.schemas.research import ResearchPlan
from app.schemas.review import ReviewResult
from app.schemas.review import ReportSection


SOURCE_QUALITY_WEIGHTS = {"high": 1.0, "medium": 0.6, "low": 0.3}


def calculate_metrics(
    plan: ResearchPlan,
    report: ResearchReport,
    review: ReviewResult,
    evidence_by_id: dict[str, EvidenceCard],
) -> QualityMetrics:
    """Calculate every score from structured data, never from Reviewer scoring."""

    claim_review_by_id = {
        item.claim_id: item for item in review.claim_support_results
    }
    evidence_review_by_pair = {
        (item.claim_id, item.evidence_id): item
        for item in review.evidence_support_results
    }

    required_claims = []
    for claim in report.claims:
        claim_review = claim_review_by_id.get(claim.claim_id)
        reviewer_requires_citation = (
            claim_review is not None
            and claim_review.requires_citation_override is True
        )
        if claim.requires_citation or reviewer_requires_citation:
            required_claims.append(claim)

    covered_required_claims = sum(
        1
        for claim in required_claims
        if any(evidence_id in evidence_by_id for evidence_id in claim.evidence_ids)
    )
    citation_coverage = _ratio(covered_required_claims, len(required_claims))

    citation_pairs = [
        (claim.claim_id, evidence_id)
        for claim in report.claims
        for evidence_id in claim.evidence_ids
    ]
    supported_pairs = sum(
        1
        for pair in citation_pairs
        if pair in evidence_review_by_pair
        and evidence_review_by_pair[pair].status.value == "supported"
    )
    citation_precision = _ratio(supported_pairs, len(citation_pairs))

    factual_claims = [
        claim for claim in report.claims if claim.claim_type.value == "factual"
    ]
    supported_factual_claims = sum(
        1
        for claim in factual_claims
        if claim.claim_id in claim_review_by_id
        and claim_review_by_id[claim.claim_id].status.value == "supported"
    )
    claim_support_rate = _ratio(
        supported_factual_claims, len(factual_claims)
    )

    covered_requirement_ids = {
        item.requirement_id for item in review.requirement_checks if item.covered
    }
    requirement_coverage = _ratio(
        sum(
            1
            for requirement in plan.requirements
            if requirement.requirement_id in covered_requirement_ids
        ),
        len(plan.requirements),
    )

    cited_evidence_ids = {
        evidence_id
        for claim in report.claims
        for evidence_id in claim.evidence_ids
        if evidence_id in evidence_by_id
    }
    source_quality_score = _average(
        [
            SOURCE_QUALITY_WEIGHTS[
                evidence_by_id[evidence_id].source_quality.value
            ]
            for evidence_id in cited_evidence_ids
        ]
    )

    format_checks = [
        bool(report.title.strip()),
        bool(report.executive_summary.strip()),
        bool(report.claims),
        bool(report.recommendation.strip()),
        bool(report.risks_and_limitations),
    ]
    format_completeness = sum(format_checks) / len(format_checks)

    overall_score = (
        citation_coverage * 30
        + claim_support_rate * 30
        + source_quality_score * 15
        + requirement_coverage * 15
        + format_completeness * 10
    )
    passed = (
        overall_score >= 80
        and citation_coverage >= 0.8
        and citation_precision >= 0.8
        and claim_support_rate >= 0.8
        and len(review.unsupported_claims) == 0
        and {item.section for item in review.report_section_results}
        == set(ReportSection)
        and all(
            item.status.value == "supported"
            for item in review.report_section_results
        )
        and not review.gap_requests
    )

    return QualityMetrics(
        citation_coverage=citation_coverage,
        citation_precision=citation_precision,
        claim_support_rate=claim_support_rate,
        requirement_coverage=requirement_coverage,
        source_quality_score=source_quality_score,
        format_completeness=format_completeness,
        overall_score=overall_score,
        passed=passed,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def _average(values: list[float]) -> float:
    return 0.0 if not values else sum(values) / len(values)
