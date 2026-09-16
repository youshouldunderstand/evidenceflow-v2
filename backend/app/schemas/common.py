"""Shared constrained types and enums."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints


Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]*$",
    ),
]
NonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    VALIDATING_EVIDENCE = "VALIDATING_EVIDENCE"
    WRITING = "WRITING"
    VALIDATING_CITATIONS = "VALIDATING_CITATIONS"
    REVIEWING = "REVIEWING"
    REVISING = "REVISING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class SourceType(StrEnum):
    OFFICIAL = "official"
    PAPER = "paper"
    BLOG = "blog"
    COMMUNITY = "community"


class SourceQuality(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SupportType(StrEnum):
    DIRECT = "direct"
    PARTIAL = "partial"


class ClaimType(StrEnum):
    FACTUAL = "factual"
    RECOMMENDATION = "recommendation"
    ANALYSIS = "analysis"


class Uncertainty(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

