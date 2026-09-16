"""Deterministic Claim → Evidence → Source citation validation."""

from __future__ import annotations

import json
import re

from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.report import ResearchReport
from app.validators.results import ValidationIssue, ValidationResult


URL_PATTERN = re.compile(r"https?://", flags=re.IGNORECASE)

# 正文中的链接整体（含路径与查询串）。用于确定性剔除，而不是替换整个句子。
URL_REDACT_PATTERN = re.compile(
    r"https?://[^\s\"'<>()\[\]{}，。；：！？、]+", flags=re.IGNORECASE
)
URL_REDACTION_TOKEN = "[链接已移除]"

# 报告侧、可由 Manager 用同一批已验证证据重写修好的问题。
REPAIRABLE_CODES = frozenset(
    {
        "duplicate_claim_id",
        "manager_output_contains_url",
        "factual_claim_missing_citation",
        "unknown_evidence_id",
    }
)


def _issue(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(
        code=code, message=message, repairable=code in REPAIRABLE_CODES
    )


def redact_urls_in_value(value: object) -> tuple[object, int]:
    """递归剔除任意 JSON 值中的 URL，返回（新值, 剔除数量）。"""

    if isinstance(value, str):
        redacted, count = URL_REDACT_PATTERN.subn(URL_REDACTION_TOKEN, value)
        return redacted, count
    if isinstance(value, list):
        total = 0
        items: list[object] = []
        for item in value:
            new_item, count = redact_urls_in_value(item)
            items.append(new_item)
            total += count
        return items, total
    if isinstance(value, dict):
        total = 0
        mapping: dict[object, object] = {}
        for key, item in value.items():
            new_item, count = redact_urls_in_value(item)
            mapping[key] = new_item
            total += count
        return mapping, total
    return value, 0


def redact_report_urls(report: ResearchReport) -> tuple[ResearchReport, int]:
    """确定性移除报告所有字段中的 URL，返回（新报告, 剔除数量）。

    最终引用 URL 只由持久化 SourceRecord 渲染，报告正文里的链接对用户没有额外
    信息增益，因此剔除它不会丢失内容；关键是要记录剔除次数，不能静默处理。
    """

    payload, count = redact_urls_in_value(report.model_dump(mode="json"))
    if not count:
        return report, 0
    return ResearchReport.model_validate(payload), count


def validate_citations(
    report: ResearchReport,
    evidence_by_id: dict[str, EvidenceCard],
    sources_by_id: dict[str, SourceRecord],
) -> ValidationResult:
    """Reject fabricated references and citations detached from snapshots."""

    issues: list[ValidationIssue] = []
    claim_ids = [claim.claim_id for claim in report.claims]
    if len(claim_ids) != len(set(claim_ids)):
        issues.append(
            _issue("duplicate_claim_id", "Report contains duplicate Claim IDs")
        )

    serialized_report = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    if URL_PATTERN.search(serialized_report):
        issues.append(
            _issue(
                "manager_output_contains_url",
                "Manager output must not contain external URLs",
            )
        )

    for claim in report.claims:
        if claim.claim_type.value == "factual" and claim.requires_citation:
            if not claim.evidence_ids:
                issues.append(
                    _issue(
                        "factual_claim_missing_citation",
                        f"Factual claim {claim.claim_id} has no Evidence ID",
                    )
                )

        for evidence_id in claim.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                issues.append(
                    _issue(
                        "unknown_evidence_id",
                        (
                            f"Claim {claim.claim_id} references unknown Evidence "
                            f"{evidence_id}"
                        ),
                    )
                )
                continue

            source = sources_by_id.get(evidence.source_id)
            if source is None:
                issues.append(
                    _issue(
                        "unknown_source_id",
                        (
                            f"Evidence {evidence_id} references unknown Source "
                            f"{evidence.source_id}"
                        ),
                    )
                )
                continue
            if evidence.quote not in source.normalized_content:
                issues.append(
                    _issue(
                        "quote_missing_from_snapshot",
                        (
                            f"Evidence {evidence_id} quote is no longer present in "
                            f"Source {source.source_id}"
                        ),
                    )
                )
            elif (
                evidence.start_offset is not None
                and evidence.end_offset is not None
                and source.normalized_content[
                    evidence.start_offset : evidence.end_offset
                ]
                != evidence.quote
            ):
                issues.append(
                    _issue(
                        "quote_offset_mismatch",
                        (
                            f"Evidence {evidence_id} offsets do not select its quote "
                            f"in Source {source.source_id}"
                        ),
                    )
                )

    return ValidationResult.from_issues(issues)
