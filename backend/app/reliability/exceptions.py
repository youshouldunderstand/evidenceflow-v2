"""用于决定恢复策略的细分异常层级。"""


class AgentError(Exception):
    """EvidenceFlow 可预期执行错误的基类。"""


class ModelError(AgentError):
    """模型响应错误的基类。"""


class ParseError(ModelError):
    """模型响应无法解析。"""


class SchemaValidationError(ModelError):
    """解析后的模型响应未通过 Schema 校验。"""


class InvalidStructuredOutput(ModelError):
    """结构化输出在允许的一次修复后仍无效。"""


class ToolError(AgentError):
    """工具执行错误的基类。"""


class SearchError(ToolError):
    """搜索 Provider 调用失败。"""


class FetchError(ToolError):
    """来源无法读取。"""


class ToolTimeoutError(ToolError):
    """工具超过允许执行时间。"""


class InvalidToolArguments(ToolError):
    """工具收到无效参数。"""


class InfrastructureError(AgentError):
    """Provider 与网络基础设施错误的基类。"""


class RateLimitError(InfrastructureError):
    """Provider 因限流拒绝调用。"""


class NetworkError(InfrastructureError):
    """网络操作失败。"""


class NetworkTimeoutError(NetworkError):
    """A transport timeout with safe, structured diagnostics (no URL or headers)."""

    def __init__(self, *, timeout_phase: str, timeout_seconds: float | None):
        self.timeout_phase = timeout_phase
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Model {timeout_phase} timed out after {timeout_seconds} seconds")


class ProviderError(InfrastructureError):
    """外部 Provider 返回不可恢复错误。"""


class WorkflowError(AgentError):
    """工作流控制错误的基类。"""


class BudgetExceeded(WorkflowError):
    """下一次调用将超过工作流硬预算。"""


class ResearchBudgetReached(BudgetExceeded):
    """研究阶段应停止并把剩余预算交给写作与审核。"""


class MaxRetryExceeded(WorkflowError):
    """操作耗尽允许的重试次数。"""


class InvalidStateTransition(WorkflowError):
    """工作流尝试执行状态机不允许的转换。"""


class EvidenceValidationFailure(WorkflowError):
    """确定性校验后没有可接受证据。"""


class CitationValidationFailure(WorkflowError):
    """报告草稿未通过确定性引用校验。"""


class ReviewValidationFailure(WorkflowError):
    """Reviewer 输出未完整覆盖给定审核上下文。"""


class ResumeConflictError(Exception):
    """A task is already running or has reached a non-resumable terminal state."""
