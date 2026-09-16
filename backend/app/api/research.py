"""研究任务、状态和 SSE 事件 API。"""

from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_research_service
from app.schemas.research import (
    CreateResearchRequest,
    CreateResearchResponse,
    ResearchTaskSnapshot,
)
from app.schemas.artifacts import ResearchArtifactsResponse, ResumeResearchResponse
from app.schemas.usage import CallTraceResponse
from app.schemas.progress import ResearchProgressResponse
from app.reliability.exceptions import ResumeConflictError
from app.services.research_service import ResearchService
from app.services.event_stream import EventStreamService


router = APIRouter(prefix="/api/research", tags=["research"])


@router.post(
    "",
    response_model=CreateResearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_research(
    payload: CreateResearchRequest,
    background_tasks: BackgroundTasks,
    service: ResearchService = Depends(get_research_service),
) -> CreateResearchResponse:
    task_id = service.create_task(payload.query, payload.depth)
    background_tasks.add_task(service.run_task, task_id, payload.query)
    return CreateResearchResponse(task_id=task_id, status="PENDING")


@router.get("/{task_id}/events")
async def stream_research_events(
    task_id: str,
    request: Request,
    after: Annotated[int | None, Query(ge=0)] = None,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID")
    ] = None,
    service: ResearchService = Depends(get_research_service),
) -> StreamingResponse:
    """回放并持续推送任务事件，终态事件发送后关闭连接。"""

    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    cursor = after or 0
    if last_event_id is not None:
        try:
            cursor = int(last_event_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Last-Event-ID must be a non-negative integer",
            ) from exc
        if cursor < 0:
            raise HTTPException(
                status_code=400,
                detail="Last-Event-ID must be a non-negative integer",
            )

    event_stream = EventStreamService(service.repository)
    return StreamingResponse(
        event_stream.stream(
            task_id,
            after_sequence=cursor,
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}/trace", response_model=CallTraceResponse)
async def get_research_trace(
    task_id: str,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    service: ResearchService = Depends(get_research_service),
) -> CallTraceResponse:
    """分页返回脱敏后的模型、搜索和页面调用尝试。"""

    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    return CallTraceResponse(
        task_id=task_id,
        attempts=service.repository.get_call_attempts(
            task_id,
            after_sequence=after,
            limit=limit,
        ),
    )


@router.get("/{task_id}/progress", response_model=ResearchProgressResponse)
async def get_research_progress(
    task_id: str,
    step_limit: Annotated[int, Query(ge=0, le=100)] = 20,
    service: ResearchService = Depends(get_research_service),
) -> ResearchProgressResponse:
    """Return persisted coverage, gaps, budget and recent tool observations."""

    if service.repository.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    progress = service.repository.get_research_progress(task_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="Research progress not found")
    return ResearchProgressResponse(
        progress=progress,
        recent_steps=service.repository.get_research_steps(
            task_id, limit=step_limit
        ),
        action_stats=service.repository.summarize_research_actions(task_id),
    )


@router.get("/{task_id}/artifacts", response_model=ResearchArtifactsResponse)
async def get_research_artifacts(
    task_id: str,
    service: ResearchService = Depends(get_research_service),
) -> ResearchArtifactsResponse:
    """Return committed research artifacts even when no report exists."""

    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    collection = service.repository.get_collection(task_id)
    return ResearchArtifactsResponse(
        task_id=task_id,
        status=task.status,
        searches=collection.searches,
        sources=collection.sources,
        evidence=collection.evidence,
        progress=collection.progress,
    )


@router.post(
    "/{task_id}/resume",
    response_model=ResumeResearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def resume_research(
    task_id: str,
    background_tasks: BackgroundTasks,
    service: ResearchService = Depends(get_research_service),
) -> ResumeResearchResponse:
    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    try:
        owner = service.reserve_resume(task_id)
    except ResumeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    background_tasks.add_task(
        service.run_task,
        task_id,
        task.query,
        lease_owner=owner,
    )
    return ResumeResearchResponse(
        task_id=task_id,
        accepted=True,
        status=task.status,
    )


@router.get("/{task_id}", response_model=ResearchTaskSnapshot)
async def get_research(
    task_id: str,
    service: ResearchService = Depends(get_research_service),
) -> ResearchTaskSnapshot:
    task = service.repository.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Research task not found")
    return task
