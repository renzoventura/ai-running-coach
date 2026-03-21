"""Onboarding endpoint — saves user profile and encrypted Garmin credentials."""
import logging
import os

from fastapi import APIRouter, HTTPException

from models.schemas import OnboardRequest, OnboardResponse
from services.dynamodb import save_credentials, save_user_profile
from services.kms import encrypt_password

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/onboard", response_model=OnboardResponse)
def onboard(request: OnboardRequest) -> OnboardResponse:
    """
    Save a new user's profile and Garmin credentials.

    The Garmin password is encrypted with AWS KMS before being stored in DynamoDB.
    """
    kms_key_id = os.environ.get("KMS_KEY_ID")
    if not kms_key_id:
        logger.error("KMS_KEY_ID environment variable is not set")
        raise HTTPException(status_code=500, detail="Server configuration error.")

    try:
        encrypted_password = encrypt_password(request.garmin_password, kms_key_id)
    except RuntimeError:
        logger.error("Failed to encrypt credentials for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Failed to secure credentials. Please try again.")

    saved_credentials = save_credentials(
        user_id=request.user_id,
        garmin_email=request.garmin_email,
        garmin_password_encrypted=encrypted_password,
        kms_key_id=kms_key_id,
    )
    if not saved_credentials:
        logger.error("Failed to save credentials for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Failed to save credentials. Please try again.")

    saved_profile = save_user_profile(
        user_id=request.user_id,
        goal_race=request.goal_race,
        target_time=request.target_time,
        training_days=request.training_days,
    )
    if not saved_profile:
        logger.error("Failed to save profile for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Failed to save profile. Please try again.")

    return OnboardResponse(success=True, message="Onboarding complete.")
