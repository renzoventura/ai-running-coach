"""User management endpoints — clear conversation, delete user data."""
import logging

from fastapi import APIRouter, HTTPException

from models.schemas import DeleteResponse, UserStatusResponse
from services.dynamodb import clear_chat_history, delete_user_data, get_user_profile

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/user/status", response_model=UserStatusResponse)
def get_user_status(user_id: str) -> UserStatusResponse:
    """
    Return the user's onboarding status.

    Frontend should call this on load to decide which screen to show:
    - "not_found" → show Connect Garmin screen
    - "garmin_connected" → show chat (onboarding agent will guide them)
    - "complete" → show chat (coaching agent)

    Query param:
        user_id: Clerk userId of the authenticated user.
    """
    profile = get_user_profile(user_id)
    if not profile:
        return UserStatusResponse(onboarding_status="not_found", data_source="garmin")
    status = profile.get("onboardingStatus", "garmin_connected")
    data_source = profile.get("dataSource", "garmin")
    return UserStatusResponse(onboarding_status=status, data_source=data_source)


@router.delete("/conversation", response_model=DeleteResponse)
def clear_conversation(user_id: str) -> DeleteResponse:
    """
    Delete all chat history for a user.

    Clears all CHAT# items from DynamoDB. The user's profile and
    Garmin credentials are preserved — they do not need to re-onboard.

    Query param:
        user_id: Clerk userId of the authenticated user.
    """
    success = clear_chat_history(user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to clear conversation. Please try again.")
    logger.info("Cleared conversation for user %s", user_id)
    return DeleteResponse(success=True, message="Conversation cleared.")


@router.delete("/user", response_model=DeleteResponse)
def delete_user(user_id: str) -> DeleteResponse:
    """
    Delete all data for a user from DynamoDB.

    Removes profile, Garmin credentials, chat history, and training plan.
    The user will need to complete onboarding again after this.

    Note: This does not delete the user's Clerk account. Handle Clerk
    account deletion on the frontend using Clerk's deleteUser() method.

    Query param:
        user_id: Clerk userId of the authenticated user.
    """
    success = delete_user_data(user_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete user data. Please try again.")
    logger.info("Deleted all data for user %s", user_id)
    return DeleteResponse(success=True, message="User data deleted. Please complete onboarding again.")
