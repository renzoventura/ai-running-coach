"""Activities endpoint — returns cached Garmin activity records for calendar display."""
import logging

from fastapi import APIRouter

from models.schemas import ActivitiesResponse, ActivitySummary
from services.dynamodb import get_cached_activities

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/activities", response_model=ActivitiesResponse)
def get_activities(user_id: str, since: str | None = None) -> ActivitiesResponse:
    """
    Return cached Garmin activity records for a user.

    Reads from DynamoDB only — no Garmin API call. Activities are cached
    automatically each time the coaching agent runs get_recent_activities.

    Use this on calendar load to show checkmarks on days where a run was recorded.

    Query params:
        user_id: Clerk userId of the authenticated user.
        since: Optional ISO date (YYYY-MM-DD). Only return activities on or after this date.
    """
    raw = get_cached_activities(user_id, since_date=since)
    activities = []
    for item in raw:
        date = item.get("date")
        activity_type = item.get("type")
        distance_km = item.get("distance_km")
        if not date or not activity_type or distance_km is None:
            continue
        activities.append(ActivitySummary(
            date=date,
            type=activity_type,
            distance_km=float(distance_km),
            duration_min=item.get("elapsed_time_min"),
            avg_pace=item.get("avg_pace_per_km"),
        ))
    logger.info("GET /activities returning %d records for user %s", len(activities), user_id)
    return ActivitiesResponse(activities=activities)
