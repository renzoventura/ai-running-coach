"""Pydantic request and response schemas."""
from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_id: str
    message: str
    timezone: str = "Australia/Melbourne"  # IANA timezone string e.g. "America/New_York"


class ChatResponse(BaseModel):
    response: str


class ConnectGarminRequest(BaseModel):
    user_id: str
    garmin_email: str
    garmin_password: str


class ConnectGarminResponse(BaseModel):
    success: bool
    message: str


class HealthResponse(BaseModel):
    status: str


class DeleteResponse(BaseModel):
    success: bool
    message: str


class ChatMessage(BaseModel):
    role: str       # "user" or "assistant"
    message: str
    timestamp: str  # ISO datetime string


class ChatHistoryResponse(BaseModel):
    messages: list[ChatMessage]


class PlanDay(BaseModel):
    date: str          # ISO format: "2026-03-23"
    week_start: str    # ISO format: "2026-03-23" (Monday of that week)
    type: str          # intervals | tempo | threshold | fartlek | easy | long | rest
    distance: float    # kilometres (0.0 for rest days)
    description: str   # e.g. "10 min WU, 6 × 1km @ 4:00/km with 90s rest, 10 min CD"


class PlanWeek(BaseModel):
    week_start: str
    days: list[PlanDay]


class GeneratePlanRequest(BaseModel):
    user_id: str


class GeneratePlanResponse(BaseModel):
    week: PlanWeek


class GetPlanResponse(BaseModel):
    weeks: list[PlanWeek]


class UserStatusResponse(BaseModel):
    onboarding_status: str  # "not_found" | "garmin_connected" | "complete"


class ActivitySummary(BaseModel):
    date: str           # YYYY-MM-DD
    type: str           # e.g. "running", "cycling"
    distance_km: float
    duration_min: int | None = None
    avg_pace: str | None = None   # "M:SS" per km


class ActivitiesResponse(BaseModel):
    activities: list[ActivitySummary]
