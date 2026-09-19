"""结构化 LLM 抽象、GLM-5.2 客户端和显式 Fake 客户端。"""

from __future__ import annotations

import json
import re
from typing import Literal, Protocol, TypeVar, cast, runtime_checkable

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.agents.contracts import (
    EvidenceCandidate,
    EvidenceExtractionBatch,
    SearchQuery,
    SearchQueryBatch,
)
from app.demo_data import DEMO_QUOTE
from app.reliability.exceptions import (
    NetworkError,
    NetworkTimeoutError,
    ParseError,
    ProviderError,
    RateLimitError,
    SchemaValidationError,
)
from app.schemas.report import ReportClaim, ResearchReport
from app.schemas.research import ResearchPlan, ResearchQuestion, ResearchRequirement
from app.schemas.review import (
    ClaimSupportResult,
    EvidenceSupportResult,
    ReportSectionResult,
    RequirementCheck,
    ReviewResult,
)
from app.schemas.usage import ModelResponse, ModelUsage, UsageMeasurement


TModel = TypeVar("TModel", bound=BaseModel)


class StructuredLLM(Protocol):
    async def generate(
        self,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> TModel: ...


class ToolFunction(BaseModel):
    name: str = Field(min_length=1)
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["function"]
    function: ToolFunction


class ToolMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


@runtime_checkable
class ToolCallingLLM(Protocol):
    async def chat_with_tools(
        self, messages: list[dict], tools: list[dict],
    ) -> ToolMessage: ...


@runtime_checkable
class UsageAwareStructuredLLM(Protocol):
    async def generate_with_usage(
        self,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> ModelResponse[TModel]: ...


@runtime_checkable
class UsageAwareToolCallingLLM(Protocol):
    async def chat_with_tools_with_usage(
        self, messages: list[dict], tools: list[dict],
    ) -> ModelResponse[ToolMessage]: ...


class OpenAICompatibleStructuredLLM:
    """GLM-compatible JSON 与原生工具调用客户端，重试由可靠性层负责。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_output_tokens: int = 4096,
        timeout_seconds: float = 180,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ProviderError("OPENAI_API_KEY is required for GLM mode")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens 必须至少为 1")
        self._api_key = api_key
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._model = model
        self._max_output_tokens = max_output_tokens
        if not 0 < timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be in (0, 600]")
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(10, timeout_seconds))
        self._transport = transport

    async def generate(
        self,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> TModel:
        response = await self.generate_with_usage(
            output_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return response.message

    async def generate_with_usage(
        self,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> ModelResponse[TModel]:
        schema_json = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        constrained_prompt = (
            f"{user_prompt}\n\nOUTPUT_JSON_SCHEMA:\n{schema_json}\n"
            "Return one JSON object only."
        )
        request_body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": constrained_prompt},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
            "temperature": 0.1,
            "max_tokens": self._max_output_tokens,
            "stream": False,
        }

        provider_response = await self._request(request_body)
        message = provider_response.message
        content = message.get("content")
        if not isinstance(content, str):
            raise ParseError("GLM response did not contain structured content")
        try:
            parsed = output_model.model_validate_json(content)
            return ModelResponse(
                message=parsed,
                usage=provider_response.usage,
                model=provider_response.model,
                provider_request_id=provider_response.provider_request_id,
            )
        except ValidationError as exc:
            raise SchemaValidationError(
                f"GLM output failed {output_model.__name__} validation: {exc}"
            ) from exc

    async def chat_with_tools(
        self, messages: list[dict], tools: list[dict],
    ) -> ToolMessage:
        response = await self.chat_with_tools_with_usage(messages, tools)
        return response.message

    async def chat_with_tools_with_usage(
        self, messages: list[dict], tools: list[dict],
    ) -> ModelResponse[ToolMessage]:
        provider_response = await self._request({
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "thinking": {"type": "enabled", "clear_thinking": False},
            "reasoning_effort": "high",
            "temperature": 0.1,
            "max_tokens": self._max_output_tokens,
            "stream": False,
        })
        message = provider_response.message
        try:
            if message.get("tool_calls") is None:
                message = {**message, "tool_calls": []}
            parsed = ToolMessage.model_validate(message)
            ids = [call.id for call in parsed.tool_calls]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate tool call IDs")
            return ModelResponse(
                message=parsed,
                usage=provider_response.usage,
                model=provider_response.model,
                provider_request_id=provider_response.provider_request_id,
            )
        except (ValidationError, ValueError) as exc:
            raise ParseError("Invalid assistant tool-call message") from exc

    async def _request(self, request_body: dict) -> ModelResponse[dict]:
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
        except httpx.TimeoutException as exc:
            timeout_phase = {
                httpx.ReadTimeout: "read", httpx.ConnectTimeout: "connect",
                httpx.WriteTimeout: "write", httpx.PoolTimeout: "pool",
            }.get(type(exc), "unknown")
            raise NetworkTimeoutError(
                timeout_phase=timeout_phase,
                timeout_seconds=getattr(self._timeout, timeout_phase, None),
            ) from exc
        except httpx.RequestError as exc:
            raise NetworkError("GLM network request failed") from exc

        if response.status_code == 429:
            raise RateLimitError("GLM returned HTTP 429")
        if response.status_code >= 400:
            raise ProviderError(f"GLM returned HTTP {response.status_code}")

        try:
            payload = response.json()
            choice = payload["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise ParseError("GLM response was incomplete")
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
            return ModelResponse(
                message=message,
                usage=_parse_provider_usage(payload.get("usage")),
                model=(
                    str(payload["model"])
                    if isinstance(payload.get("model"), str)
                    else self._model
                ),
                provider_request_id=(
                    response.headers.get("x-request-id")
                    or (
                        str(payload["id"])
                        if isinstance(payload.get("id"), str)
                        else None
                    )
                ),
            )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ParseError("GLM response did not contain a message") from exc


class DemoStructuredLLM:
    """确定性 Fake 模型，绝不冒充真实 GLM 响应。"""

    async def generate(
        self,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> TModel:
        del system_prompt
        if output_model is ResearchPlan:
            query = _extract_marker(user_prompt, "USER_QUERY") or "Demo research"
            return cast(
                TModel,
                ResearchPlan(
                    objective=query,
                    requirements=[
                        ResearchRequirement(
                            requirement_id="R001",
                            text="Base factual conclusions on validated evidence.",
                        )
                    ],
                    questions=[
                        ResearchQuestion(
                            question_id="Q001",
                            text="What does the available source establish?",
                        )
                    ],
                    comparison_dimensions=["evidence reliability"],
                    expected_output="A structured evidence-backed Phase 2 report",
                ),
            )
        if output_model is SearchQueryBatch:
            return cast(
                TModel,
                SearchQueryBatch(
                    queries=[SearchQuery(question_id="Q001", query="EvidenceFlow demo")]
                ),
            )
        if output_model is EvidenceExtractionBatch:
            return cast(
                TModel,
                EvidenceExtractionBatch(
                    candidates=[
                        EvidenceCandidate(
                            question_id="Q001",
                            evidence_summary=(
                                "Factual claims must reference validated evidence "
                                "with an original quote."
                            ),
                            quote=DEMO_QUOTE,
                            locator="Evidence validation",
                            support_type="direct",
                        )
                    ]
                ),
            )
        if output_model is ReviewResult:
            plan_json = _extract_block(user_prompt, "PLAN_JSON_START", "PLAN_JSON_END")
            report_json = _extract_block(
                user_prompt, "REPORT_JSON_START", "REPORT_JSON_END"
            )
            evidence_json = _extract_block(
                user_prompt, "EVIDENCE_JSON_START", "EVIDENCE_JSON_END"
            )
            try:
                plan_data = json.loads(plan_json)
                report_data = json.loads(report_json)
                evidence_data = json.loads(evidence_json)
                known_evidence_ids = {
                    str(item["evidence_id"]) for item in evidence_data
                }
                claims = report_data["claims"]
                requirements = plan_data["requirements"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ProviderError("Demo reviewer received malformed context") from exc

            evidence_support: list[EvidenceSupportResult] = []
            claim_support: list[ClaimSupportResult] = []
            unsupported_claims: list[str] = []
            for claim in claims:
                claim_id = str(claim["claim_id"])
                evidence_ids = [str(item) for item in claim["evidence_ids"]]
                pair_statuses: list[str] = []
                for evidence_id in evidence_ids:
                    status = (
                        "supported"
                        if evidence_id in known_evidence_ids
                        else "unsupported"
                    )
                    pair_statuses.append(status)
                    evidence_support.append(
                        EvidenceSupportResult(
                            claim_id=claim_id,
                            evidence_id=evidence_id,
                            status=status,
                            reason=(
                                "The deterministic demo evidence is present."
                                if status == "supported"
                                else "The referenced evidence is unavailable."
                            ),
                        )
                    )
                claim_status = (
                    "supported"
                    if pair_statuses and all(item == "supported" for item in pair_statuses)
                    else "unsupported"
                )
                if claim_status == "unsupported":
                    unsupported_claims.append(claim_id)
                claim_support.append(
                    ClaimSupportResult(
                        claim_id=claim_id,
                        status=claim_status,
                        requires_citation_override=None,
                        reason=(
                            "All referenced demo evidence is available."
                            if claim_status == "supported"
                            else "The claim lacks available supporting evidence."
                        ),
                    )
                )

            return cast(
                TModel,
                ReviewResult(
                    unsupported_claims=unsupported_claims,
                    overclaimed_claims=[],
                    citation_issues=[],
                    missing_dimensions=[],
                    revision_instructions=(
                        []
                        if not unsupported_claims
                        else ["Remove claims that lack available evidence."]
                    ),
                    claim_support_results=claim_support,
                    evidence_support_results=evidence_support,
                    requirement_checks=[
                        RequirementCheck(
                            requirement_id=str(item["requirement_id"]),
                            covered=True,
                            reason="Covered by the deterministic demo report.",
                        )
                        for item in requirements
                    ],
                    report_section_results=[
                        ReportSectionResult(
                            section=section,
                            status="supported",
                            related_claim_ids=[
                                str(item["claim_id"]) for item in claims
                            ],
                            reason=(
                                "The deterministic demo section is consistent "
                                "with its reviewed claims."
                            ),
                        )
                        for section in (
                            "executive_summary",
                            "recommendation",
                            "comparison_table",
                        )
                    ],
                    gap_requests=[],
                ),
            )
        if output_model is ResearchReport:
            evidence_json = _extract_block(
                user_prompt, "EVIDENCE_JSON_START", "EVIDENCE_JSON_END"
            )
            try:
                evidence_items = json.loads(evidence_json)
                first = evidence_items[0]
                evidence_id = first["evidence_id"]
                summary = first["evidence_summary"]
            except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
                raise ProviderError("Demo report requires at least one evidence card") from exc
            return cast(
                TModel,
                ResearchReport(
                    title="EvidenceFlow Phase 2 Fake Provider Report",
                    executive_summary=(
                        "This deterministic report proves the Phase 2 evidence chain; "
                        "it is not an internet research result."
                    ),
                    claims=[
                        ReportClaim(
                            claim_id="C001",
                            text=str(summary),
                            dimension="evidence reliability",
                            claim_type="factual",
                            requires_citation=True,
                            evidence_ids=[str(evidence_id)],
                            uncertainty="low",
                        )
                    ],
                    comparison_table=[],
                    recommendation="Use real providers before drawing external conclusions.",
                    risks_and_limitations=[
                        "The current run uses deterministic fake source data."
                    ],
                    unverified_notice=None,
                ),
            )
        raise ProviderError(f"Demo model cannot produce {output_model.__name__}")

    async def generate_with_usage(
        self,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> ModelResponse[TModel]:
        message = await self.generate(
            output_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        return ModelResponse(
            message=message,
            usage=ModelUsage.known_zero("deterministic demo fake"),
            model="demo-fake",
        )


def _extract_marker(text: str, marker: str) -> str:
    match = re.search(rf"{re.escape(marker)}:\s*(.+)", text)
    return match.group(1).strip() if match else ""


def _parse_provider_usage(value: object) -> ModelUsage:
    if not isinstance(value, dict):
        return ModelUsage.unknown("provider response did not include usage")

    prompt_tokens = _nonnegative_int_or_none(value.get("prompt_tokens"))
    completion_tokens = _nonnegative_int_or_none(value.get("completion_tokens"))
    total_tokens = _nonnegative_int_or_none(value.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return ModelUsage.unknown("provider usage did not contain token counts")
    return ModelUsage(
        measurement=UsageMeasurement.ACTUAL,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_reason="model pricing is not configured",
    )


def _nonnegative_int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _extract_block(text: str, start_marker: str, end_marker: str) -> str:
    pattern = rf"{re.escape(start_marker)}\s*(.*?)\s*{re.escape(end_marker)}"
    match = re.search(pattern, text, flags=re.DOTALL)
    return match.group(1) if match else ""
