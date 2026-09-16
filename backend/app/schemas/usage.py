"""V2 调用级 usage 与账本数据合同。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Identifier, NonEmptyStr


class UsageMeasurement(StrEnum):
    """Token 或费用数值的来源质量。"""

    ACTUAL = "actual"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class CallAttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CallKind(StrEnum):
    MODEL = "model"
    SEARCH = "search"
    PAGE = "page"


class ModelUsage(BaseModel):
    """一次模型响应的 Provider usage，不把未知值伪装为零。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    measurement: UsageMeasurement
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cost_amount: float | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    cost_reason: str | None = None

    @model_validator(mode="after")
    def validate_measurement(self) -> "ModelUsage":
        token_values = (
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
        )
        if self.measurement == UsageMeasurement.UNKNOWN and any(
            value is not None for value in token_values
        ):
            raise ValueError("unknown usage cannot contain token values")
        if self.cost_amount is None and not self.cost_reason:
            raise ValueError("unknown cost requires cost_reason")
        if self.cost_amount is not None and not self.cost_currency:
            raise ValueError("known cost requires cost_currency")
        return self

    @classmethod
    def unknown(cls, reason: str) -> "ModelUsage":
        return cls(
            measurement=UsageMeasurement.UNKNOWN,
            cost_reason=reason,
        )

    @classmethod
    def known_zero(cls, reason: str) -> "ModelUsage":
        return cls(
            measurement=UsageMeasurement.ACTUAL,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_amount=0,
            cost_currency="USD",
            cost_reason=reason,
        )


TMessage = TypeVar("TMessage")


class ModelResponse(BaseModel, Generic[TMessage]):
    """模型消息与其调用元数据的不可分割返回值。"""

    model_config = ConfigDict(extra="forbid")

    message: TMessage
    usage: ModelUsage
    model: str | None = None
    provider_request_id: str | None = None


class CallAttempt(BaseModel):
    """可公开查询的单次真实调用尝试。"""

    model_config = ConfigDict(extra="forbid")

    attempt_id: Identifier
    sequence: int = Field(gt=0)
    logical_call_id: Identifier
    task_id: Identifier
    step_id: str | None = None
    attempt_number: int = Field(ge=1)
    call_kind: CallKind
    phase: NonEmptyStr
    budget_phase: str | None = None
    operation_name: str | None = None
    arguments_hash: str | None = None
    status: CallAttemptStatus
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None
    error_type: str | None = None
    result_ref: str | None = None
    usage: ModelUsage | None = None
    reserved_prompt_tokens: int | None = Field(default=None, ge=0)
    reserved_completion_tokens: int | None = Field(default=None, ge=0)
    duration_ms: float | None = Field(default=None, ge=0)
    created_at: datetime | None = None


class CallTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    attempts: list[CallAttempt]
