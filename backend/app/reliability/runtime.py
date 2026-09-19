"""为模型、搜索和网页读取提供统一的可靠性控制。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from app.reliability.exceptions import (
    BudgetExceeded,
    InvalidStructuredOutput,
    MaxRetryExceeded,
    NetworkError,
    NetworkTimeoutError,
    ParseError,
    RateLimitError,
    ResearchBudgetReached,
    SchemaValidationError,
    SearchError,
    ToolTimeoutError,
)
from app.schemas.eval import FailureInjectionConfig
from app.schemas.usage import ModelResponse, ModelUsage, UsageMeasurement
from app.services.llm import (
    StructuredLLM,
    ToolCallingLLM,
    ToolMessage,
    UsageAwareStructuredLLM,
    UsageAwareToolCallingLLM,
)
from app.workflow.budget import WorkflowBudget, WorkflowUsage


T = TypeVar("T")
TModel = TypeVar("TModel", bound=BaseModel)
CallKind = Literal["model", "search", "page"]
CallPhase = Literal["started", "completed", "failed"]


@dataclass(frozen=True)
class RetryPolicy:
    """限定可恢复错误的重试次数与退避参数。"""

    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    jitter_seconds: float = 0.1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts 必须至少为 1")


@dataclass(frozen=True)
class ModelReservation:
    prompt_tokens: int
    completion_tokens: int


class FailureInjector:
    """仅供测试显式启用的故障注入器，生产默认关闭。"""

    def __init__(
        self,
        config: FailureInjectionConfig | None = None,
        *,
        enabled: bool = False,
        random_source: random.Random | None = None,
    ) -> None:
        self._config = config or FailureInjectionConfig()
        self._enabled = enabled
        self._random = random_source or random.Random()

    def before_call(self, kind: CallKind) -> None:
        """在真实调用前按配置注入一个确定类型的失败。"""

        if not self._enabled:
            return
        if self._hit(self._config.rate_limit_rate):
            raise RateLimitError("测试注入：Provider 触发 429 限流")
        if kind == "model":
            if self._hit(self._config.llm_timeout_rate):
                raise NetworkError("测试注入：模型调用超时")
            if self._hit(self._config.invalid_output_rate):
                raise SchemaValidationError("测试注入：模型结构化输出无效")
        elif kind == "search" and self._hit(self._config.search_failure_rate):
            raise SearchError("测试注入：搜索失败")
        elif kind == "page" and self._hit(self._config.fetch_timeout_rate):
            raise ToolTimeoutError("测试注入：网页读取超时")

    def _hit(self, rate: float) -> bool:
        return rate > 0 and self._random.random() < rate


class ExecutionRuntime:
    """持有单次工作流的预算、用量、重试和故障注入策略。"""

    def __init__(
        self,
        budget: WorkflowBudget | None = None,
        usage: WorkflowUsage | None = None,
        *,
        retry_policy: RetryPolicy | None = None,
        failure_injector: FailureInjector | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        random_source: random.Random | None = None,
        retry_observer: (
            Callable[[CallKind, int, Exception, float], None] | None
        ) = None,
        call_observer: (
            Callable[[CallPhase, CallKind, int, dict[str, object]], None] | None
        ) = None,
        model_output_reserve_tokens: int = 4096,
        phase: str = "general",
        phase_model_calls: int = 0,
    ) -> None:
        self.budget = budget or WorkflowBudget()
        self.usage = usage or WorkflowUsage()
        self.retry_policy = retry_policy or RetryPolicy()
        self.failure_injector = failure_injector or FailureInjector()
        self._sleeper = sleeper
        self._random = random_source or random.Random()
        self._retry_observer = retry_observer
        self._call_observer = call_observer
        self._phase = phase
        if phase_model_calls < 0:
            raise ValueError("phase_model_calls cannot be negative")
        self._phase_model_calls = phase_model_calls
        self._research_stop_reason: str | None = None
        if model_output_reserve_tokens < 1:
            raise ValueError("model_output_reserve_tokens 必须至少为 1")
        self._model_output_reserve_tokens = model_output_reserve_tokens

    async def generate(
        self,
        llm: StructuredLLM,
        output_model: type[TModel],
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> TModel:
        """执行一次结构化生成，并在结构错误时最多重新生成一次。"""

        async def call(current_user_prompt: str, *, repair: bool) -> TModel:
            async def invoke() -> ModelResponse[TModel]:
                if isinstance(llm, UsageAwareStructuredLLM):
                    return await llm.generate_with_usage(
                        output_model,
                        system_prompt=system_prompt,
                        user_prompt=current_user_prompt,
                    )
                message = await llm.generate(
                    output_model,
                    system_prompt=system_prompt,
                    user_prompt=current_user_prompt,
                )
                return ModelResponse(
                    message=message,
                    usage=ModelUsage.unknown(
                        "LLM implementation did not expose provider usage"
                    ),
                )

            response = await self._retry_call(
                "model",
                invoke,
                prompt_text=(
                    f"{system_prompt}\n{current_user_prompt}\n"
                    f"{json.dumps(output_model.model_json_schema(), ensure_ascii=False)}"
                ),
                metadata={
                    "output_model": output_model.__name__,
                    "repair": repair,
                },
            )
            return response.message

        try:
            return await call(user_prompt, repair=False)
        except (ParseError, SchemaValidationError):
            repair_prompt = (
                f"{user_prompt}\n\n"
                "REPAIR_REQUEST: 上一次输出无法通过 JSON 解析或 Schema 校验。"
                "请重新生成完整 JSON 对象，只输出符合指定 Schema 的 JSON。"
            )
            try:
                return await call(repair_prompt, repair=True)
            except (ParseError, SchemaValidationError) as exc:
                raise InvalidStructuredOutput(
                    "结构化输出在一次修复后仍然无效"
                ) from exc

    async def chat_with_tools(
        self,
        llm: ToolCallingLLM,
        messages: list[dict],
        tools: list[dict],
        *,
        metadata: dict[str, object] | None = None,
    ) -> ToolMessage:
        """Account for every tool-selection turn, including retries and history."""
        async def invoke() -> ModelResponse[ToolMessage]:
            if isinstance(llm, UsageAwareToolCallingLLM):
                return await llm.chat_with_tools_with_usage(messages, tools)
            message = await llm.chat_with_tools(messages, tools)
            return ModelResponse(
                message=message,
                usage=ModelUsage.unknown(
                    "LLM implementation did not expose provider usage"
                ),
            )

        response = await self._retry_call(
            "model",
            invoke,
            prompt_text=json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False),
            metadata={
                "output_model": "ToolMessage",
                "repair": False,
                **dict(metadata or {}),
            },
        )
        return response.message

    async def search(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        metadata: dict[str, object] | None = None,
    ) -> T:
        """在搜索调用前执行预算检查，并只重试可恢复错误。"""

        return await self._retry_call("search", operation, metadata=metadata)

    async def read_page(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        metadata: dict[str, object] | None = None,
    ) -> T:
        """在网页读取前执行预算检查，并只重试可恢复错误。"""

        return await self._retry_call("page", operation, metadata=metadata)

    async def _retry_call(
        self,
        kind: CallKind,
        operation: Callable[[], Awaitable[T]],
        *,
        prompt_text: str = "",
        metadata: dict[str, object] | None = None,
    ) -> T:
        last_error: Exception | None = None
        logical_call_id = f"CALL_{uuid4().hex[:12]}"
        arguments_hash = _arguments_hash(prompt_text, metadata)
        for attempt in range(self.retry_policy.max_attempts):
            try:
                reservation = self._reserve_before_call(kind, prompt_text=prompt_text)
            except BudgetExceeded:
                if last_error is None:
                    raise
                raise MaxRetryExceeded(
                    f"{kind} retry budget exhausted after {attempt} actual attempts"
                ) from last_error
            attempt_number = attempt + 1
            attempt_id = f"ATTEMPT_{uuid4().hex[:12]}"
            started_at = time.perf_counter()
            started_timestamp = datetime.now(timezone.utc)
            base_payload: dict[str, object] = {
                **dict(metadata or {}),
                "logical_call_id": logical_call_id,
                "attempt_id": attempt_id,
                "arguments_hash": arguments_hash,
                "started_at": started_timestamp.isoformat(),
                "budget_phase": self._phase,
            }
            if reservation is not None:
                base_payload["reserved_prompt_tokens"] = reservation.prompt_tokens
                base_payload["reserved_completion_tokens"] = reservation.completion_tokens
            self._observe_call(
                "started", kind, attempt_number, base_payload
            )
            try:
                self.failure_injector.before_call(kind)
                result = await operation()
            except Exception as exc:
                retryable = isinstance(
                    exc, (RateLimitError, NetworkError, ToolTimeoutError)
                )
                self._observe_call(
                    "failed",
                    kind,
                    attempt_number,
                    {
                        **base_payload,
                        "error_type": type(exc).__name__,
                        **({
                            "timeout_phase": exc.timeout_phase,
                            "timeout_seconds": exc.timeout_seconds,
                        } if isinstance(exc, NetworkTimeoutError) else {}),
                        "retryable": retryable,
                        "duration_ms": duration_ms(started_at),
                        "ended_at": datetime.now(timezone.utc).isoformat(),
                        **self._failed_usage_payload(kind),
                    },
                )
                if not retryable:
                    raise
                last_error = exc
                if attempt + 1 >= self.retry_policy.max_attempts:
                    break
                delay = min(
                    self.retry_policy.max_delay_seconds,
                    self.retry_policy.base_delay_seconds * (2**attempt),
                )
                delay += self._random.random() * self.retry_policy.jitter_seconds
                if self._retry_observer is not None:
                    self._retry_observer(kind, attempt + 1, exc, delay)
                await self._sleeper(delay)
            else:
                completed_payload = {
                    **base_payload,
                    "duration_ms": duration_ms(started_at),
                    "ended_at": datetime.now(timezone.utc).isoformat(),
                }
                if kind == "model":
                    usage = (
                        result.usage
                        if isinstance(result, ModelResponse)
                        else ModelUsage.unknown(
                            "model operation did not return a usage envelope"
                        )
                    )
                    self._settle_model_usage(usage, reservation)
                    completed_payload.update(
                        {
                            "usage": usage.model_dump(mode="json"),
                            "model": (
                                result.model
                                if isinstance(result, ModelResponse)
                                else None
                            ),
                            "provider_request_id": (
                                result.provider_request_id
                                if isinstance(result, ModelResponse)
                                else None
                            ),
                        }
                    )
                self._observe_call(
                    "completed",
                    kind,
                    attempt_number,
                    completed_payload,
                )
                return result
        raise MaxRetryExceeded(
            f"{kind} 调用在 {self.retry_policy.max_attempts} 次尝试后仍失败"
        ) from last_error

    def _observe_call(
        self,
        phase: CallPhase,
        kind: CallKind,
        attempt: int,
        payload: dict[str, object],
    ) -> None:
        if self._call_observer is not None:
            self._call_observer(phase, kind, attempt, payload)

    def _reserve_before_call(
        self, kind: CallKind, *, prompt_text: str
    ) -> ModelReservation | None:
        """先检查再占用预算，确保超限时真实调用尚未发生。"""

        if kind == "model":
            if self.usage.model_calls >= self.budget.max_model_calls:
                raise BudgetExceeded("模型调用次数预算已耗尽")
            if self._phase == "research":
                if self._phase_model_calls >= self.budget.research_max_model_calls:
                    self._research_stop_reason = "research_model_limit"
                    raise ResearchBudgetReached(
                        "研究阶段模型调用额度已达到，保留预算用于写作和审核"
                    )
                available_before_downstream = (
                    self.budget.max_model_calls
                    - self.budget.downstream_reserved_model_calls
                )
                if self.usage.model_calls >= available_before_downstream:
                    self._research_stop_reason = "downstream_model_reserve"
                    raise ResearchBudgetReached(
                        "研究阶段已到下游模型调用预留边界"
                    )
            if self._phase == "supplement":
                if (
                    self._phase_model_calls
                    >= self.budget.supplement_max_model_calls
                ):
                    self._research_stop_reason = "supplement_model_limit"
                    raise ResearchBudgetReached("补证阶段模型调用额度已达到")
                available_before_post_supplement = (
                    self.budget.max_model_calls
                    - self.budget.post_supplement_reserved_model_calls
                )
                if self.usage.model_calls >= available_before_post_supplement:
                    self._research_stop_reason = "post_supplement_model_reserve"
                    raise ResearchBudgetReached(
                        "补证阶段已到修订和复审模型调用预留边界"
                    )
            estimated_prompt_tokens = _estimate_tokens(prompt_text)
            consumed_tokens = (
                self.usage.prompt_tokens + self.usage.completion_tokens
            )
            required_tokens = (
                estimated_prompt_tokens + self._model_output_reserve_tokens
            )
            if consumed_tokens + required_tokens > self.budget.max_tokens:
                raise BudgetExceeded("Token 预算将在本次模型调用前被超出")
            if (
                self._phase == "research"
                and consumed_tokens + required_tokens
                > self.budget.max_tokens - self.budget.downstream_reserved_tokens
            ):
                self._research_stop_reason = "downstream_token_reserve"
                raise ResearchBudgetReached(
                    "研究阶段已到下游 Token 预留边界"
                )
            if (
                self._phase == "supplement"
                and consumed_tokens + required_tokens
                > self.budget.max_tokens
                - self.budget.post_supplement_reserved_tokens
            ):
                self._research_stop_reason = "post_supplement_token_reserve"
                raise ResearchBudgetReached(
                    "补证阶段已到修订和复审 Token 预留边界"
                )
            self.usage.model_calls += 1
            self.usage.prompt_tokens += estimated_prompt_tokens
            self.usage.completion_tokens += self._model_output_reserve_tokens
            if self._phase in {"research", "supplement"}:
                self._phase_model_calls += 1
            return ModelReservation(
                prompt_tokens=estimated_prompt_tokens,
                completion_tokens=self._model_output_reserve_tokens,
            )

        if self.usage.tool_calls >= self.budget.max_tool_calls:
            raise BudgetExceeded("工具调用次数预算已耗尽")
        if kind == "search":
            if self.usage.search_calls >= self.budget.max_search_calls:
                raise BudgetExceeded("搜索调用次数预算已耗尽")
            self.usage.search_calls += 1
        else:
            if self.usage.pages_read >= self.budget.max_pages:
                raise BudgetExceeded("网页读取次数预算已耗尽")
            self.usage.pages_read += 1
        self.usage.tool_calls += 1
        return None

    @property
    def research_stop_reason(self) -> str | None:
        return self._research_stop_reason

    @property
    def research_model_calls(self) -> int:
        return self._phase_model_calls

    def stop_research(self, reason: str) -> bool:
        if (
            self._phase in {"research", "supplement"}
            and self._research_stop_reason is None
        ):
            self._research_stop_reason = reason
            return True
        return self._phase == "research"

    def _failed_usage_payload(self, kind: CallKind) -> dict[str, object]:
        if kind != "model":
            return {}
        self.usage.unknown_model_attempts += 1
        usage = ModelUsage.unknown(
            "model attempt failed without provider usage"
        )
        return {"usage": usage.model_dump(mode="json")}

    def _settle_model_usage(
        self,
        usage: ModelUsage,
        reservation: ModelReservation | None,
    ) -> None:
        if usage.measurement == UsageMeasurement.ACTUAL:
            self.usage.actual_model_attempts += 1
            if usage.prompt_tokens is not None:
                self.usage.actual_prompt_tokens += usage.prompt_tokens
                if reservation is not None:
                    self.usage.prompt_tokens = max(
                        0,
                        self.usage.prompt_tokens
                        - reservation.prompt_tokens
                        + usage.prompt_tokens,
                    )
            if usage.completion_tokens is not None:
                self.usage.actual_completion_tokens += usage.completion_tokens
                if reservation is not None:
                    self.usage.completion_tokens = max(
                        0,
                        self.usage.completion_tokens
                        - reservation.completion_tokens
                        + usage.completion_tokens,
                    )
            return
        if usage.measurement == UsageMeasurement.ESTIMATED:
            self.usage.estimated_model_attempts += 1
            return
        self.usage.unknown_model_attempts += 1

    def state_usage(self) -> dict[str, int | float]:
        """返回可以安全写回 LangGraph State 的普通字段。"""

        return {
            "model_calls": self.usage.model_calls,
            "tool_calls": self.usage.tool_calls,
            "search_calls": self.usage.search_calls,
            "pages_read": self.usage.pages_read,
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "actual_prompt_tokens": self.usage.actual_prompt_tokens,
            "actual_completion_tokens": self.usage.actual_completion_tokens,
            "actual_model_attempts": self.usage.actual_model_attempts,
            "estimated_model_attempts": self.usage.estimated_model_attempts,
            "unknown_model_attempts": self.usage.unknown_model_attempts,
            "estimated_cost": self.usage.estimated_cost,
        }


def _estimate_tokens(text: str) -> int:
    """使用保守的 UTF-8 字节估算值，避免依赖特定模型分词器。"""

    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _arguments_hash(
    prompt_text: str, metadata: dict[str, object] | None
) -> str:
    value = prompt_text or json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def duration_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)
