"""Strava OAuth callback and token refresh endpoints."""
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from models.schemas import StravaRefreshRequest, StravaRefreshResponse
from services.dynamodb import (
    create_profile,
    get_strava_credentials,
    save_strava_credentials,
    update_profile_field,
)
from services.strava import StravaClient

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/auth/strava/callback")
def strava_callback(
    code: str = Query(...),
    state: str = Query(..., description="Clerk user_id passed as the Strava OAuth state param"),
    error: str | None = Query(None),
) -> RedirectResponse:
    """
    Strava OAuth callback — called by Strava after the athlete authorises.

    Exchanges the authorization code for tokens, saves credentials to DynamoDB,
    creates a user profile with onboardingStatus='garmin_connected' and
    dataSource='strava', then redirects the browser to the frontend chat URL.

    The frontend must initiate the OAuth flow by redirecting the user to:
        https://www.strava.com/oauth/authorize
            ?client_id=<STRAVA_CLIENT_ID>
            &redirect_uri=<API_BASE>/auth/strava/callback
            &response_type=code
            &approval_prompt=auto
            &scope=activity:read_all
            &state=<clerk_user_id>
    """
    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")

    if error:
        logger.warning("Strava OAuth denied for user %s: %s", state, error)
        return RedirectResponse(url=f"{frontend_url}?strava_error={error}")

    user_id = state
    redirect_uri = os.environ.get("STRAVA_REDIRECT_URI", "")
    try:
        client = StravaClient()
        tokens = client.exchange_code(code, redirect_uri=redirect_uri)
    except RuntimeError as e:
        logger.error("Strava code exchange failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=502, detail="Failed to authenticate with Strava.")

    save_strava_credentials(
        user_id=user_id,
        athlete_id=tokens["athlete_id"],
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=tokens["expires_at"],
    )

    # Reuse the existing onboarding flow — "garmin_connected" means "data source linked"
    create_profile(user_id, onboarding_status="garmin_connected", data_source="strava")
    # Pre-populate name from Strava athlete profile
    if tokens.get("athlete_name"):
        update_profile_field(user_id, "name", tokens["athlete_name"])

    # Pre-cache recent activities so the calendar is populated before first chat
    try:
        from services.dynamodb import save_activities
        activities = client.get_recent_activities(tokens["access_token"], days=28)
        if activities:
            save_activities(user_id, activities)
            logger.info("Pre-cached %d Strava activities for user %s", len(activities), user_id)
    except Exception:
        logger.warning("Failed to pre-cache Strava activities for user %s — non-fatal", user_id)

    logger.info("Strava connected for user %s (athlete %s)", user_id, tokens["athlete_id"])
    return RedirectResponse(url=f"{frontend_url}/chat")


@router.post("/auth/strava/refresh", response_model=StravaRefreshResponse)
def strava_refresh(request: StravaRefreshRequest) -> StravaRefreshResponse:
    """
    Refresh a Strava access token if it has expired.

    Fetches stored credentials, checks expiry (with a 5-minute buffer),
    and refreshes the access token via Strava's token endpoint if needed.
    Updates DynamoDB with the new token and expiry.

    This endpoint is called automatically by the chat stream when a Strava
    user's token is about to expire. Frontends do not need to call it directly.
    """
    creds = get_strava_credentials(request.user_id)
    if not creds:
        raise HTTPException(status_code=404, detail="No Strava credentials found for this user.")

    # Only refresh if actually expired (5-minute buffer to avoid edge cases)
    if int(time.time()) < creds["expires_at"] - 300:
        return StravaRefreshResponse(refreshed=False, message="Token is still valid.")

    try:
        client = StravaClient()
        new_tokens = client.refresh_access_token(creds["refresh_token"])
    except RuntimeError as e:
        logger.error("Strava refresh failed for user %s: %s", request.user_id, e)
        raise HTTPException(status_code=502, detail="Failed to refresh Strava token.")

    save_strava_credentials(
        user_id=request.user_id,
        athlete_id=creds["athlete_id"],
        access_token=new_tokens["access_token"],
        refresh_token=creds["refresh_token"],  # refresh token does not rotate
        expires_at=new_tokens["expires_at"],
    )
    logger.info("Strava token refreshed for user %s", request.user_id)
    return StravaRefreshResponse(refreshed=True, message="Token refreshed successfully.")
