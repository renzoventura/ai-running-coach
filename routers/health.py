"""Health check endpoint."""
from fastapi import APIRouter

from models.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """Return service health status."""
    return HealthResponse(status="ok")
