"""Internal recovery records; these are never exposed by public APIs."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.common import Identifier, NonEmptyStr
from app.schemas.progress import ResearchObservation


class DecisionStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


class OperationStatus(StrEnum):
    STARTED = "started"
    INCOMPLETE = "incomplete"
    RESULT_SAVED = "result_saved"
    COMMITTED = "committed"


class ResearchDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: Identifier
    task_id: Identifier
    conversation_ref: Identifier
    turn_index: int = Field(ge=1)
    input_messages: list[dict[str, object]]
    assistant_message: dict[str, object]
    status: DecisionStatus
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ResearchOperationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: Identifier
    task_id: Identifier
    decision_id: Identifier
    turn_index: int = Field(ge=1)
    operation_index: int = Field(ge=0)
    tool_call_id: NonEmptyStr
    tool_name: NonEmptyStr
    arguments: dict[str, object]
    status: OperationStatus
    result_payload: dict[str, object] | None = None
    observation: ResearchObservation | None = None
    uncertainty_reason: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
