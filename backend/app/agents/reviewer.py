"""Reviewer Agent：只有语义审核能力，不持有研究工具。"""

from __future__ import annotations

import json

from app.agents.prompts import REVIEWER_SYSTEM_PROMPT
from app.reliability.runtime import ExecutionRuntime
from app.reliability.exceptions import ReviewValidationFailure
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.report import ResearchReport
from app.schemas.progress import ResearchProgress
from app.schemas.research import ResearchPlan
from app.schemas.review import ReviewResult
from app.services.llm import StructuredLLM
from app.services.source_context import evidence_source_metadata
from app.validators.review_validator import validate_review_result


class ReviewerAgent:
    """Reviewer 持有 LLM，但不接收 SearchProvider 或 WebReader。

    ``max_uncited_evidence`` 控制是否把「未被任何 Claim 引用」的证据也交给审核。
    给出它们，Reviewer 才有能力发现**选择性引用**（只引支持自己的那半边）；
    但把大集合全部塞进去会撑爆审核上下文，因此超过阈值时退回只给被引用证据。
    """

    def __init__(
        self,
        llm: StructuredLLM,
        *,
        include_source_context: bool = True,
        max_uncited_evidence: int = 10,
    ) -> None:
        if max_uncited_evidence < 0:
            raise ValueError("max_uncited_evidence cannot be negative")
        self._llm = llm
        self._include_source_context = include_source_context
        self._max_uncited_evidence = max_uncited_evidence

    async def review(
        self,
        plan: ResearchPlan,
        report: ResearchReport,
        evidence: list[EvidenceCard],
        sources: list[SourceRecord],
        runtime: ExecutionRuntime | None = None,
        *,
        progress: ResearchProgress | None = None,
    ) -> ReviewResult:
        # Citation Validator 已保证报告中的 Evidence ID 有效。除被引用的证据外，
        # 在数量可控时也把未引用证据交给 Reviewer，否则它结构上无法发现
        # 「选择性引用」。超过阈值时退回只给被引用证据，避免上下文膨胀。
        evidence_by_id = {item.evidence_id: item for item in evidence}
        cited_evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for claim in report.claims
                for evidence_id in claim.evidence_ids
            )
        )
        cited_set = set(cited_evidence_ids)
        cited_evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in cited_evidence_ids
            if evidence_id in evidence_by_id
        ]
        uncited_evidence = [
            item for item in evidence if item.evidence_id not in cited_set
        ]
        uncited_included = (
            0 < len(uncited_evidence) <= self._max_uncited_evidence
        )
        review_evidence = cited_evidence + (
            uncited_evidence if uncited_included else []
        )
        source_by_id = {item.source_id: item for item in sources}
        evidence_context = [
            (_evidence_review_context(item, source_by_id.get(item.source_id))
             if self._include_source_context else item.model_dump(mode="json"))
            for item in review_evidence
        ]
        source_metadata = evidence_source_metadata(review_evidence, sources)
        if not self._include_source_context:
            source_metadata = []
        required_pairs = [
            [claim.claim_id, evidence_id]
            for claim in report.claims
            for evidence_id in claim.evidence_ids
        ]
        review_scope = {
            "required_claim_ids": [item.claim_id for item in report.claims],
            "required_claim_evidence_pairs": required_pairs,
            "required_requirement_ids": [
                item.requirement_id for item in plan.requirements
            ],
            "required_report_sections": [
                "executive_summary",
                "recommendation",
                "comparison_table",
            ],
            "cited_evidence_ids": cited_evidence_ids,
            "uncited_evidence_ids": (
                [item.evidence_id for item in uncited_evidence]
                if uncited_included
                else []
            ),
            "uncited_evidence_included": uncited_included,
        }
        prompt = (
            "REVIEW_SCOPE_JSON_START\n"
            f"{json.dumps(review_scope, ensure_ascii=False)}\n"
            "REVIEW_SCOPE_JSON_END\n"
            "PLAN_JSON_START\n"
            f"{plan.model_dump_json()}\n"
            "PLAN_JSON_END\n"
            "REPORT_JSON_START\n"
            f"{report.model_dump_json()}\n"
            "REPORT_JSON_END\n"
            "EVIDENCE_JSON_START\n"
            f"{json.dumps(evidence_context, ensure_ascii=False)}\n"
            "EVIDENCE_JSON_END\n"
            "SOURCE_METADATA_JSON_START\n"
            f"{json.dumps(source_metadata, ensure_ascii=False)}\n"
            "SOURCE_METADATA_JSON_END\n"
            "CONFLICTS_JSON_START\n"
            f"{json.dumps([item.model_dump(mode='json') for item in progress.conflicts] if progress is not None else [], ensure_ascii=False)}\n"
            "CONFLICTS_JSON_END"
        )
        active_runtime = runtime or ExecutionRuntime()
        review = await active_runtime.generate(
            self._llm,
            ReviewResult,
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            user_prompt=prompt,
        )
        validation = validate_review_result(plan, report, review)
        if validation.valid:
            return review

        # Schema 合法不代表审核项完整。把确定性校验发现的缺失/未知 ID 明确反馈给
        # Reviewer，并只允许再生成一次完整结果，避免无限自我修复循环。
        validation_errors = [
            {"code": issue.code, "message": issue.message}
            for issue in validation.issues
        ]
        completeness_repair_prompt = (
            f"{prompt}\n"
            "PREVIOUS_REVIEW_JSON_START\n"
            f"{review.model_dump_json()}\n"
            "PREVIOUS_REVIEW_JSON_END\n"
            "DETERMINISTIC_VALIDATION_ERRORS_START\n"
            f"{json.dumps(validation_errors, ensure_ascii=False)}\n"
            "DETERMINISTIC_VALIDATION_ERRORS_END\n"
            "Regenerate the entire ReviewResult. Correct every deterministic "
            "validation error and preserve all already required review entries."
        )
        repaired = await active_runtime.generate(
            self._llm,
            ReviewResult,
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            user_prompt=completeness_repair_prompt,
        )
        repaired_validation = validate_review_result(plan, report, repaired)
        if not repaired_validation.valid:
            raise ReviewValidationFailure(
                "; ".join(
                    issue.message for issue in repaired_validation.issues
                )
            )
        return repaired


def _evidence_review_context(
    evidence: EvidenceCard,
    source: SourceRecord | None,
    *,
    radius: int = 400,
) -> dict[str, object]:
    payload = evidence.model_dump(mode="json")
    if source is None:
        return {
            **payload,
            "context_start_offset": None,
            "context_end_offset": None,
            "context_before": None,
            "context_after": None,
        }
    quote_start = evidence.start_offset
    quote_end = evidence.end_offset
    if quote_start is None or quote_end is None:
        quote_start = source.normalized_content.find(evidence.quote)
        quote_end = (
            quote_start + len(evidence.quote) if quote_start >= 0 else -1
        )
    if quote_start < 0 or quote_end < 0:
        return {
            **payload,
            "context_start_offset": None,
            "context_end_offset": None,
            "context_before": None,
            "context_after": None,
        }
    context_start = max(0, quote_start - radius)
    context_end = min(len(source.normalized_content), quote_end + radius)
    return {
        **payload,
        "context_start_offset": context_start,
        "context_end_offset": context_end,
        "context_before": source.normalized_content[context_start:quote_start],
        "context_after": source.normalized_content[quote_end:context_end],
    }
