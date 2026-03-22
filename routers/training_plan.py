"""Training plan endpoints — generate and retrieve weekly training plans."""
import logging
from collections import defaultdict

from fastapi import APIRouter, HTTPException

from agent.agent import generate_plan
from models.schemas import (
    GeneratePlanRequest,
    GetPlanResponse,
    PlanDay,
    PlanWeek,
)
from services.dynamodb import get_credentials, get_plan_days, get_user_profile, save_plan_day
from services.garmin import GarminClient
from services.kms import decrypt_password

logger = logging.getLogger(__name__)
router = APIRouter()


def _auth_garmin(user_id: str) -> GarminClient:
    """Fetch credentials, decrypt, and return a connected GarminClient. Raises HTTPException on failure."""
    credentials = get_credentials(user_id)
    if not credentials:
        raise HTTPException(
            status_code=404,
            detail="User credentials not found. Please complete onboarding first.",
        )
    try:
        plaintext_password = decrypt_password(credentials["garminPasswordEncrypted"])
    except RuntimeError:
        logger.error("Failed to decrypt credentials for user %s", user_id)
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve Garmin credentials. Please try again.",
        )
    garmin_client = GarminClient()
    if not garmin_client.connect(credentials["garminEmail"], plaintext_password):
        logger.error("Garmin Connect authentication failed for user %s", user_id)
        raise HTTPException(
            status_code=503,
            detail="Unable to connect to Garmin. Please check your credentials and try again.",
        )
    return garmin_client


@router.post("/training-plan/generate", response_model=GetPlanResponse)
def generate_training_plan(request: GeneratePlanRequest) -> GetPlanResponse:
    """
    Generate a complete multi-week training block for the user.

    Calculates the plan length from the user's goal and race date, fetches Garmin
    data, runs the Strands agent to produce the full block as JSON, saves one
    DynamoDB item per day, and returns all weeks.
    """
    user_profile = get_user_profile(request.user_id)
    if not user_profile:
        raise HTTPException(
            status_code=404,
            detail="User profile not found. Please complete onboarding first.",
        )

    garmin_client = _auth_garmin(request.user_id)

    try:
        days_raw = generate_plan(
            user_id=request.user_id,
            garmin_client=garmin_client,
            user_profile=user_profile,
        )
    except ValueError as e:
        logger.error("Plan generation failed for user %s: %s", request.user_id, e)
        raise HTTPException(status_code=500, detail="Failed to generate training plan. Please try again.")
    except Exception:
        logger.exception("Unexpected error during plan generation for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Failed to generate training plan. Please try again.")

    grouped: dict[str, list[PlanDay]] = defaultdict(list)
    for day_dict in days_raw:
        save_plan_day(request.user_id, day_dict)
        grouped[day_dict["week_start"]].append(PlanDay(**day_dict))

    weeks = [PlanWeek(week_start=ws, days=days) for ws, days in sorted(grouped.items())]
    logger.info("Generated %d-week plan (%d days) for user %s", len(weeks), len(days_raw), request.user_id)
    return GetPlanResponse(weeks=weeks)


@router.get("/training-plan", response_model=GetPlanResponse)
def get_training_plan(user_id: str) -> GetPlanResponse:
    """
    Return all saved training plan days for the user, grouped by week.

    Query param:
        user_id: Clerk userId of the authenticated user.
    """
    raw_days = get_plan_days(user_id)

    grouped: dict[str, list[PlanDay]] = defaultdict(list)
    for d in raw_days:
        grouped[d["week_start"]].append(PlanDay(**d))

    weeks = [
        PlanWeek(week_start=ws, days=days)
        for ws, days in sorted(grouped.items())
    ]

    return GetPlanResponse(weeks=weeks)
