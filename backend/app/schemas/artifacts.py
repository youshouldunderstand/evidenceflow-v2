"""Persisted task artifacts available before a final report exists."""

from pydantic import BaseModel, ConfigDict

from app.schemas.common import Identifier, TaskStatus
from app.schemas.evidence import EvidenceCard, SourceRecord
from app.schemas.progress import ResearchProgress
from app.schemas.research import SearchRecord


class ResearchArtifactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    status: TaskStatus
    searches: list[SearchRecord]
    sources: list[SourceRecord]
    evidence: list[EvidenceCard]
    progress: ResearchProgress | None


class ResumeResearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: Identifier
    accepted: bool
    status: TaskStatus
