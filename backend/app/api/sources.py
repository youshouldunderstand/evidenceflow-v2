"""Read-only APIs for inspecting persisted source snapshots."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import get_research_service
from app.schemas.passage import SourcePassagesResponse
from app.services.research_service import ResearchService
from app.services.source_passages import PASSAGE_EXTRACTION_VERSION, source_passages


router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("/{source_id}/passages", response_model=SourcePassagesResponse)
async def get_source_passages(
    source_id: str,
    query: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    context: Annotated[int, Query(ge=0, le=3)] = 1,
    service: ResearchService = Depends(get_research_service),
) -> SourcePassagesResponse:
    source = service.repository.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source snapshot not found")
    return SourcePassagesResponse(
        source_id=source.source_id,
        content_hash=source.content_hash,
        source_extraction_version=source.extraction_version,
        passage_version=PASSAGE_EXTRACTION_VERSION,
        query=query,
        passages=source_passages(
            source,
            query=query,
            offset=offset,
            limit=limit,
            context=context,
        ),
    )
