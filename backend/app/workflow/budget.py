"""工作流预算合同。

Phase 4 在真实调用前执行硬拦截；所有节点共享同一组限制与用量字段。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkflowBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_model_calls: int = Field(default=8, gt=0)
    max_tool_calls: int = Field(default=30, gt=0)
    max_search_calls: int = Field(default=8, gt=0)
    max_pages: int = Field(default=15, gt=0)
    max_tokens: int = Field(default=50_000, gt=0)
    research_max_model_calls: int = Field(default=4, gt=0)
    downstream_reserved_model_calls: int = Field(default=2, ge=0)
    downstream_reserved_tokens: int = Field(default=12_000, ge=0)
    max_no_progress_steps: int = Field(default=2, gt=0)
    max_supplement_rounds: int = Field(default=1, ge=0)
    supplement_max_model_calls: int = Field(default=2, gt=0)
    post_supplement_reserved_model_calls: int = Field(default=2, ge=0)
    post_supplement_reserved_tokens: int = Field(default=8_000, ge=0)


class WorkflowUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    search_calls: int = Field(default=0, ge=0)
    pages_read: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    actual_prompt_tokens: int = Field(default=0, ge=0)
    actual_completion_tokens: int = Field(default=0, ge=0)
    actual_model_attempts: int = Field(default=0, ge=0)
    estimated_model_attempts: int = Field(default=0, ge=0)
    unknown_model_attempts: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
