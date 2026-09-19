"""Researcher Agent：唯一允许搜索和读取来源的角色。"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone

from app.agents.contracts import (
    EvidenceExtractionBatch,
    RejectedEvidence,
    ResearchCollection,
    SearchQueryBatch,
)
from app.agents.prompts import (
    RESEARCHER_EVIDENCE_SYSTEM_PROMPT,
    RESEARCHER_QUERY_SYSTEM_PROMPT,
)
from app.agents.research_tools import (
    ResearchRecoveryStore,
    ToolPage,
    collect_tool_pages,
    research_conversation_id,
)
from app.agents.progress_tracker import (
    apply_validated_evidence,
    create_research_progress,
    refresh_progress,
)
from app.reliability.exceptions import (
    FetchError,
    MaxRetryExceeded,
    NetworkError,
    ResearchBudgetReached,
)
from app.reliability.runtime import ExecutionRuntime
from app.schemas.common import SourceQuality, SourceType
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.progress import ResearchObservation, ResearchProgress
from app.schemas.research import ResearchPlan, SearchRecord
from app.services.llm import StructuredLLM, ToolCallingLLM
from app.tools.web_reader import WebReader
from app.tools.web_search import SearchProvider, SearchResult
from app.tools.url_identity import normalize_resource_url, url_fragment
from app.validators.evidence_validator import normalize_content, validate_evidence


class ResearcherAgent:
    def __init__(
        self,
        llm: StructuredLLM,
        search_provider: SearchProvider,
        web_reader: WebReader,
        *,
        max_results_per_query: int = 3,
        query_system_prompt: str = RESEARCHER_QUERY_SYSTEM_PROMPT,
        evidence_system_prompt: str = RESEARCHER_EVIDENCE_SYSTEM_PROMPT,
    ) -> None:
        if max_results_per_query < 1:
            raise ValueError("max_results_per_query must be positive")
        self._llm = llm
        self._search_provider = search_provider
        self._web_reader = web_reader
        self._max_results_per_query = max_results_per_query
        self._query_system_prompt = query_system_prompt
        self._evidence_system_prompt = evidence_system_prompt

    async def collect(
        self,
        task_id: str,
        plan: ResearchPlan,
        runtime: ExecutionRuntime | None = None,
        checkpoint: (
            Callable[
                [ResearchProgress, ResearchObservation | None, ResearchCollection],
                None,
            ]
            | None
        ) = None,
        prior_collection: ResearchCollection | None = None,
        research_directive: str | None = None,
        recovery_store: ResearchRecoveryStore | None = None,
    ) -> ResearchCollection:
        active_runtime = runtime or ExecutionRuntime(phase="research")
        starting_usage = active_runtime.usage.model_copy(deep=True)
        searches = list(prior_collection.searches) if prior_collection else []
        errors = list(prior_collection.errors) if prior_collection else []
        validation_feedback: list[str] = []
        sources = list(prior_collection.sources) if prior_collection else []
        accepted = list(prior_collection.evidence) if prior_collection else []
        rejected = (
            list(prior_collection.rejected_evidence) if prior_collection else []
        )
        progress = (
            prior_collection.progress.model_copy(deep=True)
            if prior_collection is not None
            and prior_collection.progress is not None
            else create_research_progress(task_id, plan, active_runtime)
        )
        conversation_ref = research_conversation_id(
            task_id, research_directive
        )
        previous_conversation = progress.conversation_ref
        previous_stop_reason = progress.stop_reason
        for source in sources:
            source_evidence = [
                item for item in accepted if item.source_id == source.source_id
            ]
            apply_validated_evidence(
                progress, source, source_evidence, active_runtime
            )
        if (
            prior_collection is not None
            and previous_conversation == conversation_ref
            and previous_stop_reason is not None
        ):
            progress.stop_reason = previous_stop_reason
            return ResearchCollection(
                searches=searches,
                sources=sources,
                evidence=accepted,
                rejected_evidence=rejected,
                errors=errors,
                model_calls=0,
                tool_calls=0,
                search_calls=0,
                pages_read=0,
                stop_reason=previous_stop_reason,
                progress=progress,
            )
        progress.conversation_ref = conversation_ref
        progress.stop_reason = None
        progress.no_progress_streak = 0

        def current_collection() -> ResearchCollection:
            return ResearchCollection(
                searches=list(searches),
                sources=list(sources),
                evidence=list(accepted),
                rejected_evidence=list(rejected),
                errors=list(errors),
                model_calls=(
                    active_runtime.usage.model_calls - starting_usage.model_calls
                ),
                tool_calls=(
                    active_runtime.usage.tool_calls - starting_usage.tool_calls
                ),
                search_calls=(
                    active_runtime.usage.search_calls - starting_usage.search_calls
                ),
                pages_read=(
                    active_runtime.usage.pages_read - starting_usage.pages_read
                ),
                stop_reason=active_runtime.research_stop_reason,
                progress=progress.model_copy(deep=True),
            )

        def save_checkpoint(observation: ResearchObservation | None = None) -> None:
            refresh_progress(progress, active_runtime)
            if checkpoint is not None:
                checkpoint(progress.model_copy(deep=True), observation, current_collection())

        save_checkpoint()
        if isinstance(self._llm, ToolCallingLLM):
            pages = collect_tool_pages(
                self._llm, self._search_provider, self._web_reader,
                task_id, plan, active_runtime, searches, errors,
                self._max_results_per_query,
                progress,
                validation_feedback,
                observation_observer=save_checkpoint,
                progress_observer=save_checkpoint,
                source_lookup=lambda source_id: next(
                    (
                        item
                        for item in sources
                        if item.source_id == source_id
                    ),
                    None,
                ),
                evidence_lookup=lambda evidence_id: next(
                    (
                        item
                        for item in accepted
                        if item.evidence_id == evidence_id
                    ),
                    None,
                ),
                research_directive=research_directive,
                conversation_ref=conversation_ref,
                recovery_store=recovery_store,
                source_by_url=lambda url: next(
                    (item for item in sources if item.url == url), None
                ),
            )
        else:
            # Explicit deterministic/legacy structured providers retain their fixtures.
            pages = self._collect_legacy_pages(
                task_id,
                plan,
                active_runtime,
                searches,
                errors,
                research_directive,
            )
        known_question_ids = {item.question_id for item in plan.questions}
        existing_evidence_ids = {item.evidence_id for item in accepted}
        existing_quote_keys = {
            (item.source_id, item.quote) for item in accepted
        }
        async for tool_page in pages:
            result = tool_page.result
            page = tool_page.page
            known_urls = {url for search in searches for url in search.result_urls}
            extraction_content = normalize_content(page.extracted_text)
            if tool_page.source is None:
                content_hash = hashlib.sha256(
                    page.raw_content.encode("utf-8")
                ).hexdigest()
                normalized_url = normalize_resource_url(result.url)
                source = SourceRecord(
                    source_id=_stable_id("S", task_id, normalized_url, content_hash),
                    task_id=task_id,
                    search_id=_search_id_for_url(searches, result.url),
                    title=page.title or result.title,
                    url=result.url,
                    source_type=result.source_type,
                    retrieved_at=page.retrieved_at,
                    content_hash=content_hash,
                    raw_content=page.raw_content,
                    normalized_content=extraction_content,
                    normalized_url=normalized_url,
                    url_fragment=url_fragment(result.url),
                    content_type=page.content_type,
                    extraction_version=page.extraction_version,
                    source_type_reason=(
                        "source type assigned by the search adapter from URL provenance"
                    ),
                )
                sources.append(source)
            else:
                source = tool_page.source
            accepted_for_source: list[EvidenceCard] = []

            try:
                extraction = await active_runtime.generate(
                    self._llm,
                    EvidenceExtractionBatch,
                    system_prompt=self._evidence_system_prompt,
                    user_prompt=(
                        f"RESEARCH_PLAN:\n{plan.model_dump_json()}\n"
                        f"SOURCE_ID: {source.source_id}\n"
                        f"SOURCE_TITLE: {source.title}\n"
                        f"SOURCE_URL: {source.url}\n"
                        "SOURCE_NORMALIZED_CONTENT_START\n"
                        f"{extraction_content}\n"
                        "SOURCE_NORMALIZED_CONTENT_END"
                    ),
                )
            except ResearchBudgetReached:
                break
            for candidate in extraction.candidates:
                evidence_id = _stable_id(
                    "E", task_id, source.source_id, candidate.quote
                )
                quote_key = (source.source_id, candidate.quote)
                if quote_key in existing_quote_keys:
                    rejected_item = RejectedEvidence(
                        evidence_id=evidence_id,
                        source_id=source.source_id,
                        reasons=["Duplicate quote from the same source"],
                    )
                    rejected.append(rejected_item)
                    validation_feedback.append(
                        f"Evidence {evidence_id} rejected: "
                        + "; ".join(rejected_item.reasons)
                    )
                    continue
                existing_quote_keys.add(quote_key)
                start_offset = source.normalized_content.find(candidate.quote)
                end_offset = (
                    start_offset + len(candidate.quote) if start_offset >= 0 else None
                )
                evidence = EvidenceCard(
                    evidence_id=evidence_id,
                    question_id=candidate.question_id,
                    question_ids=candidate.question_ids,
                    evidence_summary=candidate.evidence_summary,
                    quote=candidate.quote,
                    source_id=source.source_id,
                    locator=candidate.locator,
                    start_offset=start_offset if start_offset >= 0 else None,
                    end_offset=end_offset,
                    support_type=candidate.support_type,
                    source_quality=_quality_for_source_type(source.source_type),
                )
                result_validation = validate_evidence(
                    evidence,
                    source,
                    known_urls,
                    existing_evidence_ids=existing_evidence_ids,
                    known_question_ids=known_question_ids,
                )
                existing_evidence_ids.add(evidence.evidence_id)
                if result_validation.valid:
                    accepted.append(evidence)
                    accepted_for_source.append(evidence)
                else:
                    rejected_item = RejectedEvidence(
                        evidence_id=evidence.evidence_id,
                        source_id=source.source_id,
                        reasons=[issue.message for issue in result_validation.issues],
                    )
                    rejected.append(rejected_item)
                    validation_feedback.append(
                        f"Evidence {evidence.evidence_id} rejected: "
                        + "; ".join(rejected_item.reasons)
                    )

            apply_validated_evidence(
                progress,
                source,
                accepted_for_source,
                active_runtime,
            )

            if not isinstance(self._llm, ToolCallingLLM):
                save_checkpoint()

        if active_runtime.research_stop_reason is None:
            if not progress.open_gaps:
                active_runtime.stop_research("coverage_sufficient")
            else:
                active_runtime.stop_research("sources_exhausted")
        refresh_progress(progress, active_runtime)
        save_checkpoint()
        return current_collection()


    async def _collect_legacy_pages(
        self, task_id: str, plan: ResearchPlan, active_runtime: ExecutionRuntime,
        searches: list[SearchRecord], errors: list[str],
        research_directive: str | None,
    ) -> AsyncIterator[ToolPage]:
        try:
            query_batch = await active_runtime.generate(
                self._llm,
                SearchQueryBatch,
                system_prompt=self._query_system_prompt,
                user_prompt=(
                    f"RESEARCH_PLAN:\n{plan.model_dump_json()}\n"
                    f"RESEARCH_DIRECTIVE:\n{research_directive or 'initial research'}"
                ),
            )
        except ResearchBudgetReached:
            return
        selected_results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for query_item in query_batch.queries:
            results = await active_runtime.search(
                lambda query=query_item.query: self._search_provider.search(query),
                metadata={"query": query_item.query},
            )
            limited_results = results[: self._max_results_per_query]
            searches.append(
                SearchRecord(
                    search_id=_stable_id("SEARCH", task_id, query_item.query),
                    task_id=task_id,
                    query=query_item.query,
                    result_urls=[item.url for item in results],
                    executed_at=datetime.now(timezone.utc),
                )
            )
            for result in limited_results:
                if result.url not in seen_urls:
                    selected_results.append(result)
                    seen_urls.add(result.url)

        for result in selected_results:
            try:
                page = await active_runtime.read_page(
                    lambda url=result.url: self._web_reader.read(url),
                    metadata={"url": result.url},
                )
            except (FetchError, NetworkError, MaxRetryExceeded) as exc:
                errors.append(f"{result.url}: {exc}")
                continue
            yield ToolPage(result=result, page=page)


def _stable_id(prefix: str, *parts: str) -> str:
    """从业务身份生成稳定 ID，使节点重放不会产生重复记录。"""

    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:12]}"


def _search_id_for_url(searches: list[SearchRecord], url: str) -> str:
    for search in searches:
        if url in search.result_urls:
            return search.search_id
    raise ValueError(f"URL is not present in any SearchRecord: {url}")


def _quality_for_source_type(source_type: SourceType) -> SourceQuality:
    if source_type in {SourceType.OFFICIAL, SourceType.PAPER}:
        return SourceQuality.HIGH
    if source_type is SourceType.BLOG:
        return SourceQuality.MEDIUM
    return SourceQuality.LOW
