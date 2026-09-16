"""Shared deterministic validation result contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.schemas.common import NonEmptyStr


class ValidationIssue(BaseModel):
    """一个确定性校验问题。

    ``repairable`` 区分两类问题，避免把「可以重写修好的格式问题」与
    「证据链本身损坏」混为一谈：

    - ``repairable=True``：仅凭同一批已验证证据重写报告即可修复，例如正文写了
      URL、事实性结论缺引用、引用了不存在的 evidence_id。这类问题允许一次有界
      的定向修订，而不是直接让整个任务失败。
    - ``repairable=False``：证据链与持久化快照不一致，Manager 无工具可修，
      必须致命失败。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyStr
    message: NonEmptyStr
    repairable: bool = False


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    issues: list[ValidationIssue]

    @classmethod
    def from_issues(cls, issues: list[ValidationIssue]) -> "ValidationResult":
        return cls(valid=not issues, issues=issues)

    @property
    def repairable_issues(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.repairable]

    @property
    def fatal_issues(self) -> list[ValidationIssue]:
        return [item for item in self.issues if not item.repairable]

    @property
    def only_repairable(self) -> bool:
        """有问题但全部可修复。"""

        return bool(self.issues) and not self.fatal_issues
