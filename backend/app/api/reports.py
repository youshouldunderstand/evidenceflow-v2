"""Phase 2 report API with programmatically rendered citation links."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_research_service
from app.schemas.phase2 import ResearchReportResponse
from app.services.research_service import ResearchService


router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/{task_id}", response_model=ResearchReportResponse)
async def get_report(
    task_id: str,
    service: ResearchService = Depends(get_research_service),
) -> ResearchReportResponse:
    report = service.repository.get_report(
        task_id, provider_notice=service.provider_notice
    )
    if report is None:
        raise HTTPException(status_code=404, detail="Research report not found")
    return report
