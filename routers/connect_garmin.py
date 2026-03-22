"""Connect Garmin endpoint — links a Garmin account to a user and starts onboarding."""
import logging
import os

from fastapi import APIRouter, HTTPException

from models.schemas import ConnectGarminRequest, ConnectGarminResponse
from services.dynamodb import create_profile, save_credentials
from services.kms import encrypt_password

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/connect-garmin", response_model=ConnectGarminResponse)
def connect_garmin(request: ConnectGarminRequest) -> ConnectGarminResponse:
    """
    Link a Garmin account to a user and initialise their profile.

    Encrypts the Garmin password with KMS, saves credentials to DynamoDB,
    and creates a user profile with onboardingStatus set to "garmin_connected".
    The user will then be guided through profile setup via the /chat/stream endpoint.
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

    if not save_credentials(
        user_id=request.user_id,
        garmin_email=request.garmin_email,
        garmin_password_encrypted=encrypted_password,
        kms_key_id=kms_key_id,
    ):
        logger.error("Failed to save credentials for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Failed to save credentials. Please try again.")

    if not create_profile(request.user_id, onboarding_status="garmin_connected"):
        logger.error("Failed to create profile for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Failed to create profile. Please try again.")

    logger.info("Garmin connected for user %s — onboarding started", request.user_id)
    return ConnectGarminResponse(success=True, message="Garmin connected. Starting onboarding.")
