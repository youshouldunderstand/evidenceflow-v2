"""Research planning, search, task, and system schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Identifier, NonEmptyStr, TaskStatus
from app.schemas.eval import ExecutionStats


class ResearchQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    text: NonEmptyStr


class ResearchRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: Identifier
    text: NonEmptyStr


class ResearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: NonEmptyStr
    requirements: list[ResearchRequirement] = Field(min_length=1)
    questions: list[ResearchQuestion] = Field(min_length=1)
    comparison_dimensions: list[NonEmptyStr] = Field(min_length=1)
    expected_output: NonEmptyStr

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ResearchPlan":
        requirement_ids = [item.requirement_id for item in self.requirements]
        question_ids = [item.question_id for item in self.questions]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("requirement_id values must be unique")
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question_id values must be unique")
        return self


class SearchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_id: Identifier
    task_id: Identifier
    query: NonEmptyStr
    result_urls: list[NonEmptyStr]
    executed_at: AwareDatetime


class CreateResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: NonEmptyStr
    depth: Literal["standard"] = "standard"


class CreateResearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    status: TaskStatus


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    app: NonEmptyStr
    workflow_version: NonEmptyStr
    # 当前实际生效的 Provider 声明。用于避免"配了真实凭据却静默跑 Fake"这类
    # 不可见的状态错配：启动后 GET /health 就能确认走的是真实还是 Fake。
    provider: NonEmptyStr | None = None
    provider_notice: NonEmptyStr | None = None
    research_service_error: NonEmptyStr | None = None


class ResearchTaskSnapshot(BaseModel):
    """任务状态、错误和可公开运行统计。"""

    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    query: NonEmptyStr
    status: TaskStatus
    workflow_version: NonEmptyStr
    error: str | None
    execution: ExecutionStats = Field(default_factory=ExecutionStats)
    is_running: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime
