"""FastAPI dependency helpers."""

from fastapi import HTTPException, Request, status

from app.services.research_service import ResearchService


def get_research_service(request: Request) -> ResearchService:
    service = getattr(request.app.state, "research_service", None)
    if service is None:
        detail = getattr(
            request.app.state,
            "research_service_error",
            "Research provider is not configured",
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return service

