"""Manager Agent：在无网络权限下规划、写作和修订。"""

from __future__ import annotations

import json

from app.agents.prompts import (
    MANAGER_CITATION_REPAIR_SYSTEM_PROMPT,
    MANAGER_PLAN_SYSTEM_PROMPT,
    MANAGER_REVISE_SYSTEM_PROMPT,
    MANAGER_WRITE_SYSTEM_PROMPT,
)
from app.reliability.runtime import ExecutionRuntime
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.report import ResearchReport
from app.schemas.progress import ResearchProgress
from app.schemas.research import ResearchPlan
from app.schemas.review import ReviewResult
from app.services.llm import StructuredLLM
from app.services.source_context import evidence_source_metadata


class ManagerAgent:
    """Manager 按设计不接收搜索或网页读取依赖。"""

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    async def plan(
        self, user_query: str, runtime: ExecutionRuntime | None = None
    ) -> ResearchPlan:
        active_runtime = runtime or ExecutionRuntime()
        return await active_runtime.generate(
            self._llm,
            ResearchPlan,
            system_prompt=MANAGER_PLAN_SYSTEM_PROMPT,
            user_prompt=f"USER_QUERY: {user_query}",
        )

    async def write(
        self,
        user_query: str,
        plan: ResearchPlan,
        evidence: list[EvidenceCard],
        runtime: ExecutionRuntime | None = None,
        *,
        progress: ResearchProgress | None = None,
        sources: list[SourceRecord] | None = None,
    ) -> ResearchReport:
        evidence_payload = [item.model_dump(mode="json") for item in evidence]
        user_prompt = (
            f"USER_QUERY: {user_query}\n"
            f"RESEARCH_PLAN:\n{plan.model_dump_json()}\n"
            "EVIDENCE_JSON_START\n"
            f"{json.dumps(evidence_payload, ensure_ascii=False)}\n"
            "EVIDENCE_JSON_END\n"
            "SOURCE_METADATA_JSON_START\n"
            f"{json.dumps(evidence_source_metadata(evidence, sources or []), ensure_ascii=False)}\n"
            "SOURCE_METADATA_JSON_END"
        )
        if progress is not None:
            user_prompt += (
                "\nRESEARCH_PROGRESS_JSON_START\n"
                f"{progress.model_dump_json()}\n"
                "RESEARCH_PROGRESS_JSON_END"
            )
        active_runtime = runtime or ExecutionRuntime()
        return await active_runtime.generate(
            self._llm,
            ResearchReport,
            system_prompt=MANAGER_WRITE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

    async def revise(
        self,
        user_query: str,
        plan: ResearchPlan,
        evidence: list[EvidenceCard],
        draft_report: ResearchReport,
        review: ReviewResult,
        runtime: ExecutionRuntime | None = None,
        *,
        sources: list[SourceRecord] | None = None,
    ) -> ResearchReport:
        evidence_payload = [item.model_dump(mode="json") for item in evidence]
        user_prompt = (
            f"USER_QUERY: {user_query}\n"
            f"RESEARCH_PLAN:\n{plan.model_dump_json()}\n"
            f"DRAFT_REPORT:\n{draft_report.model_dump_json()}\n"
            f"REVIEW_RESULT:\n{review.model_dump_json()}\n"
            "REVISION_INSTRUCTIONS_PRESENT: true\n"
            "EVIDENCE_JSON_START\n"
            f"{json.dumps(evidence_payload, ensure_ascii=False)}\n"
            "EVIDENCE_JSON_END\n"
            "SOURCE_METADATA_JSON_START\n"
            f"{json.dumps(evidence_source_metadata(evidence, sources or []), ensure_ascii=False)}\n"
            "SOURCE_METADATA_JSON_END"
        )
        active_runtime = runtime or ExecutionRuntime()
        return await active_runtime.generate(
            self._llm,
            ResearchReport,
            system_prompt=MANAGER_REVISE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

    async def repair_citations(
        self,
        user_query: str,
        plan: ResearchPlan,
        evidence: list[EvidenceCard],
        draft_report: ResearchReport,
        issues: list[dict[str, object]],
        runtime: ExecutionRuntime | None = None,
        *,
        sources: list[SourceRecord] | None = None,
    ) -> ResearchReport:
        """只修复确定性引文校验列出的问题，不改动其他内容。

        与 ``revise`` 的区别：``revise`` 由 Reviewer 的语义结论驱动，本方法由
        确定性校验问题驱动，因此不消耗评审预算、也不改变报告的语义结论。
        """

        evidence_payload = [item.model_dump(mode="json") for item in evidence]
        allowed_ids = sorted(item.evidence_id for item in evidence)
        user_prompt = (
            f"USER_QUERY: {user_query}\n"
            f"RESEARCH_PLAN:\n{plan.model_dump_json()}\n"
            f"DRAFT_REPORT:\n{draft_report.model_dump_json()}\n"
            "CITATION_VIOLATIONS_JSON_START\n"
            f"{json.dumps(issues, ensure_ascii=False)}\n"
            "CITATION_VIOLATIONS_JSON_END\n"
            "ALLOWED_EVIDENCE_IDS_JSON_START\n"
            f"{json.dumps(allowed_ids, ensure_ascii=False)}\n"
            "ALLOWED_EVIDENCE_IDS_JSON_END\n"
            "EVIDENCE_JSON_START\n"
            f"{json.dumps(evidence_payload, ensure_ascii=False)}\n"
            "EVIDENCE_JSON_END\n"
            "SOURCE_METADATA_JSON_START\n"
            f"{json.dumps(evidence_source_metadata(evidence, sources or []), ensure_ascii=False)}\n"
            "SOURCE_METADATA_JSON_END"
        )
        active_runtime = runtime or ExecutionRuntime()
        return await active_runtime.generate(
            self._llm,
            ResearchReport,
            system_prompt=MANAGER_CITATION_REPAIR_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
