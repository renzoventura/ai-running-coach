"""Activities endpoints — cached reads and on-demand sync for calendar display."""
import logging
import time

from fastapi import APIRouter, HTTPException

from models.schemas import ActivitiesResponse, ActivitySummary, ActivitySyncRequest
from services.dynamodb import (
    get_cached_activities,
    get_strava_credentials,
    get_user_profile,
    is_month_synced,
    mark_month_synced,
    save_activities,
    save_strava_credentials,
)
from services.strava import StravaClient

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/activities/sync", response_model=ActivitiesResponse)
def sync_activities(request: ActivitySyncRequest) -> ActivitiesResponse:
    """
    Fetch activities for a date range from the user's data source, cache to DynamoDB,
    and return them. Call this when the user navigates to a new calendar month.

    Request body:
        user_id: Clerk userId.
        since:   Start date inclusive (YYYY-MM-DD).
        until:   End date inclusive (YYYY-MM-DD).
    """
    profile = get_user_profile(request.user_id)
    if not profile:
        raise HTTPException(status_code=404, detail="User not found.")

    data_source = profile.get("dataSource", "garmin")
    if data_source != "strava":
        raise HTTPException(status_code=400, detail="On-demand sync is only supported for Strava users.")

    # Derive the month key from the since date (YYYY-MM)
    year_month = request.since[:7]

    # If already synced, return cached data without hitting Strava
    if is_month_synced(request.user_id, year_month):
        cached = get_cached_activities(request.user_id, since_date=request.since)
        cached = [a for a in cached if a.get("date", "") <= request.until]
        activities = [
            ActivitySummary(
                date=a["date"],
                type=a["type"],
                distance_km=float(a["distance_km"]),
                duration_min=a.get("elapsed_time_min"),
                avg_pace=a.get("avg_pace_per_km"),
            )
            for a in cached
            if a.get("date") and a.get("type") and a.get("distance_km") is not None
        ]
        logger.info("Returning %d cached activities for %s (user %s)", len(activities), year_month, request.user_id)
        return ActivitiesResponse(activities=activities)

    # Not yet synced — fetch from Strava, cache, mark as synced
    creds = get_strava_credentials(request.user_id)
    if not creds:
        raise HTTPException(status_code=404, detail="Strava credentials not found.")

    access_token = creds["access_token"]
    if time.time() >= creds["expires_at"] - 300:
        try:
            new_tokens = StravaClient().refresh_access_token(creds["refresh_token"])
            access_token = new_tokens["access_token"]
            save_strava_credentials(
                user_id=request.user_id,
                athlete_id=creds["athlete_id"],
                access_token=access_token,
                refresh_token=creds["refresh_token"],
                expires_at=new_tokens["expires_at"],
            )
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail="Failed to refresh Strava token.") from e

    fetched = StravaClient().get_activities_for_range(access_token, request.since, request.until)

    if fetched:
        save_activities(request.user_id, fetched)
    mark_month_synced(request.user_id, year_month)

    activities = [
        ActivitySummary(
            date=a["date"],
            type=a["type"],
            distance_km=float(a["distance_km"]),
            duration_min=a.get("elapsed_time_min"),
            avg_pace=a.get("avg_pace_per_km"),
        )
        for a in fetched
        if a.get("date") and a.get("type") and a.get("distance_km") is not None
    ]
    logger.info("Synced %d activities (%s) for user %s", len(activities), year_month, request.user_id)
    return ActivitiesResponse(activities=activities)


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
