"""Expert recommendation preview API."""

from fastapi import APIRouter

from app.schemas.expert_selection import (
    ExpertSelectionPreviewRequest,
    ExpertSelectionPreviewResponse,
)
from app.services.expert_selection import recommend_experts


router = APIRouter(prefix="/api/expert-selection", tags=["expert-selection"])


@router.post("/preview", response_model=ExpertSelectionPreviewResponse)
async def preview_expert_selection(
    request: ExpertSelectionPreviewRequest,
) -> ExpertSelectionPreviewResponse:
    result = recommend_experts(
        topic=request.topic.strip(),
        topic_category=request.topic_category,
    )
    return ExpertSelectionPreviewResponse(**result)
