"""Evaluation and failure-injection data contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import Identifier, NonEmptyStr


class FailureRootCause(StrEnum):
    SEARCH_MISSED_SOURCE = "search_missed_source"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    INVALID_QUOTE = "invalid_quote"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    OVERCLAIM = "overclaim"
    MISSING_CITATION = "missing_citation"
    REVIEWER_FALSE_POSITIVE = "reviewer_false_positive"
    REVIEWER_FALSE_NEGATIVE = "reviewer_false_negative"
    BUDGET_EXCEEDED = "budget_exceeded"


class FailureInjectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm_timeout_rate: float = Field(default=0, ge=0, le=1)
    invalid_output_rate: float = Field(default=0, ge=0, le=1)
    search_failure_rate: float = Field(default=0, ge=0, le=1)
    fetch_timeout_rate: float = Field(default=0, ge=0, le=1)
    rate_limit_rate: float = Field(default=0, ge=0, le=1)


class ExecutionStats(BaseModel):
    """一次任务可公开的执行用量与延迟统计。"""

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
    usage_measurement: Literal["actual", "estimated", "unknown", "mixed"] = (
        "unknown"
    )
    estimated_cost: float = Field(default=0, ge=0)
    cost_is_estimated: bool = True
    cost_amount: float | None = Field(default=None, ge=0)
    cost_currency: str | None = None
    cost_reason: str | None = "cost was not recorded"
    latency_ms: float = Field(default=0, ge=0)


class EvalRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eval_run_id: Identifier
    prompt_version: NonEmptyStr
    workflow_version: NonEmptyStr
    model: NonEmptyStr
    architecture: Literal["single_agent", "evidence_flow", "fixed_retrieval", "no_stop_policy", "no_context_review"]
    metrics: dict[str, float | None]
    dataset_version: NonEmptyStr = "v0.1"
    sample_count: int = Field(default=0, ge=0)
    provider_notice: NonEmptyStr = "Provider 未声明"


class BadCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    failure_stage: NonEmptyStr
    failure_type: NonEmptyStr
    input: NonEmptyStr
    expected: str | None
    actual: str | None
    root_cause: FailureRootCause
    prompt_version: NonEmptyStr
    workflow_version: NonEmptyStr


class QualityMetrics(BaseModel):
    """由普通程序计算的报告质量指标。"""

    model_config = ConfigDict(extra="forbid")

    citation_coverage: float = Field(ge=0, le=1)
    citation_precision: float = Field(ge=0, le=1)
    claim_support_rate: float = Field(ge=0, le=1)
    requirement_coverage: float = Field(ge=0, le=1)
    source_quality_score: float = Field(ge=0, le=1)
    format_completeness: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=100)
    passed: bool


class EvalQuestion(BaseModel):
    """一条必须依赖外部证据回答的版本化评测问题。"""

    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    category: NonEmptyStr
    query: NonEmptyStr
    expected_dimensions: list[NonEmptyStr] = Field(min_length=1)


class EvalDataset(BaseModel):
    """固定问题、版本和外部证据要求组成的评测数据集。"""

    model_config = ConfigDict(extra="forbid")

    dataset_version: NonEmptyStr
    requires_external_evidence: Literal[True] = True
    questions: list[EvalQuestion] = Field(min_length=15)

    @model_validator(mode="after")
    def validate_unique_question_ids(self) -> "EvalDataset":
        ids = [item.question_id for item in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question_id values must be unique")
        return self


class EvaluationSampleResult(BaseModel):
    """统一架构执行器返回的一条实际样本结果。"""

    model_config = ConfigDict(extra="forbid")

    question_id: Identifier
    task_id: Identifier
    architecture: Literal["single_agent", "evidence_flow", "fixed_retrieval", "no_stop_policy", "no_context_review"]
    completed: bool
    recovered: bool = False
    recovery_attempted: bool = False
    quality: QualityMetrics | None = None
    evidence_accepted: int = Field(default=0, ge=0)
    evidence_rejected: int = Field(default=0, ge=0)
    unsupported_claims: int = Field(default=0, ge=0)
    total_claims: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    tool_failures: int = Field(default=0, ge=0)
    local_tool_actions: int = Field(default=0, ge=0)
    invalid_tool_actions: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    duplicate_searches: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    # Legacy field is retained for reading old result files; never infer actual cost.
    estimated_cost: float | None = Field(default=None, ge=0)
    execution: ExecutionStats | None = None
    judge_execution: ExecutionStats | None = None
    judge_task_id: str | None = None
    judge_error: str | None = None
    control_version: str | None = None
    run_index: int = Field(default=1, ge=1)
    generated_report: dict | None = None
    outcome: Literal["completed", "partial", "no_evidence", "budget_failed", "failed", "unknown"] = "unknown"
    latency_ms: float | None = Field(default=None, ge=0)
    failure_stage: str | None = None
    failure_type: str | None = None
    # 执行器层异常的可读原因。缺失时不得假设失败原因，必须保留原始信息。
    failure_detail: str | None = None


class EvaluationComparison(BaseModel):
    """同一数据集上两种架构的实际运行汇总。"""

    model_config = ConfigDict(extra="forbid")

    dataset_version: NonEmptyStr
    provider_notice: NonEmptyStr
    evidence_flow: EvalRun
    single_agent: EvalRun
    fixed_retrieval: EvalRun | None = None
    suite_id: str | None = None
    planned_samples: int = Field(default=0, ge=0)
    stopped_reason: str | None = None
    experiment_config: dict = Field(default_factory=dict)
    ablations: dict[str, EvalRun] = Field(default_factory=dict)

    samples: list[EvaluationSampleResult] = Field(default_factory=list)
