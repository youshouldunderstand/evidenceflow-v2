"""Stable, offset-based views over a saved source snapshot."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Identifier, NonEmptyStr


class PassageMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)


class SourcePassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passage_id: Identifier
    source_id: Identifier
    paragraph_index: int = Field(ge=0)
    text: NonEmptyStr
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    locator: NonEmptyStr
    matches: list[PassageMatch] = Field(default_factory=list)


class SourcePassagesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: Identifier
    content_hash: NonEmptyStr
    source_extraction_version: NonEmptyStr
    passage_version: NonEmptyStr
    query: str | None = None
    passages: list[SourcePassage]
