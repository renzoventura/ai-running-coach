"""Chat endpoint — routes to onboarding or coaching agent based on user status."""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from agent.agent import stream_agent, stream_onboarding_agent, stream_strava_agent
from models.schemas import ChatHistoryResponse, ChatMessage, ChatRequest, ChatResponse
from services.dynamodb import (
    get_chat_history,
    get_credentials,
    get_plan_days,
    get_strava_credentials,
    get_user_profile,
    save_chat_message,
    save_strava_credentials,
)
from services.garmin import GarminClient
from services.kms import decrypt_password
from services.strava import StravaClient

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_garmin_client(user_id: str) -> GarminClient:
    """Fetch credentials, decrypt, and return a connected GarminClient."""
    credentials = get_credentials(user_id)
    if not credentials:
        raise HTTPException(status_code=404, detail="User credentials not found. Please complete onboarding first.")
    try:
        plaintext_password = decrypt_password(credentials["garminPasswordEncrypted"])
    except RuntimeError:
        logger.error("Failed to decrypt credentials for user %s", user_id)
        raise HTTPException(status_code=503, detail="Unable to retrieve Garmin credentials. Please try again.")
    garmin_client = GarminClient()
    try:
        connected = garmin_client.connect(credentials["garminEmail"], plaintext_password, user_id=user_id)
    except PermissionError:
        raise HTTPException(status_code=429, detail="Garmin is temporarily rate limiting connections. Please wait a few minutes and try again.")
    except ValueError:
        raise HTTPException(status_code=401, detail="Garmin credentials are invalid. Please reconnect your Garmin account.")
    if not connected:
        logger.error("Garmin Connect authentication failed for user %s", user_id)
        raise HTTPException(status_code=503, detail="Unable to connect to Garmin. Please try again.")
    return garmin_client


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """
    Stream the agent response token by token using Server-Sent Events.

    Routes to the onboarding agent if the user's profile is not yet complete,
    or the coaching agent if onboarding is done.

    After streaming, saves the conversation to DynamoDB. If the onboarding agent
    completes setup during this turn, triggers initial training plan generation.
    """
    profile = get_user_profile(request.user_id)
    if not profile:
        raise HTTPException(
            status_code=404,
            detail="User not found. Please connect your Garmin account first.",
        )

    onboarding_status = profile.get("onboardingStatus", "garmin_connected")
    data_source = profile.get("dataSource", "garmin")
    chat_history = get_chat_history(request.user_id, limit=20)

    async def onboarding_stream():
        full_response = []
        try:
            async for chunk in stream_onboarding_agent(
                message=request.message,
                user_id=request.user_id,
                profile=profile,
                chat_history=chat_history,
                timezone=request.timezone,
            ):
                full_response.append(chunk)
                sse_lines = "\n".join(f"data: {line}" for line in chunk.split("\n"))
                yield f"{sse_lines}\n\n"
        except Exception:
            logger.exception("Onboarding agent error for user %s", request.user_id)
            yield "data: [ERROR]\n\n"
            return

        yield "data: [DONE]\n\n"

        complete_response = "".join(full_response)
        save_chat_message(request.user_id, "user", request.message, request.user_id)
        save_chat_message(request.user_id, "assistant", complete_response, request.user_id)

        # If onboarding completed this turn, generate initial training plan in a thread
        # so it doesn't block the async event loop (Garmin auth + Bedrock calls are slow)
        updated_profile = get_user_profile(request.user_id)
        if updated_profile and updated_profile.get("onboardingStatus") == "complete":
            asyncio.get_event_loop().run_in_executor(
                None, _generate_initial_plan, request.user_id, updated_profile
            )

    async def coaching_stream():
        full_response = []
        try:
            garmin_client = _get_garmin_client(request.user_id)
        except HTTPException as e:
            if e.status_code == 404:
                # Credentials missing — hard failure, can't proceed
                yield f"data: [ERROR] {e.detail}\n\n"
                return
            # Garmin auth failed (503) — fall back to cached data
            logger.warning("Garmin unavailable for user %s — running in offline mode", request.user_id)
            garmin_client = None

        try:
            async for chunk in stream_agent(
                message=request.message,
                user_id=request.user_id,
                garmin_client=garmin_client,
                chat_history=chat_history,
                timezone=request.timezone,
            ):
                full_response.append(chunk)
                sse_lines = "\n".join(f"data: {line}" for line in chunk.split("\n"))
                yield f"{sse_lines}\n\n"
        except Exception:
            logger.exception("Coaching agent error for user %s", request.user_id)
            yield "data: [ERROR]\n\n"
            return

        yield "data: [DONE]\n\n"

        complete_response = "".join(full_response)
        save_chat_message(request.user_id, "user", request.message, request.user_id)
        save_chat_message(request.user_id, "assistant", complete_response, request.user_id)

        # Persist any refreshed OAuth tokens back to cache
        if garmin_client:
            garmin_client.persist_session(request.user_id)

    async def strava_coaching_stream():
        import time as _time
        full_response = []

        creds = get_strava_credentials(request.user_id)
        if not creds:
            yield "data: [ERROR] Strava credentials not found. Please reconnect your Strava account.\n\n"
            return

        # Auto-refresh access token if expired (5-minute buffer)
        access_token = creds["access_token"]
        if _time.time() >= creds["expires_at"] - 300:
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
            except RuntimeError:
                logger.warning("Strava token refresh failed for user %s — using existing token", request.user_id)

        try:
            async for chunk in stream_strava_agent(
                message=request.message,
                user_id=request.user_id,
                access_token=access_token,
                athlete_id=creds["athlete_id"],
                chat_history=chat_history,
                timezone=request.timezone,
            ):
                full_response.append(chunk)
                sse_lines = "\n".join(f"data: {line}" for line in chunk.split("\n"))
                yield f"{sse_lines}\n\n"
        except Exception:
            logger.exception("Strava coaching agent error for user %s", request.user_id)
            yield "data: [ERROR]\n\n"
            return

        yield "data: [DONE]\n\n"

        complete_response = "".join(full_response)
        save_chat_message(request.user_id, "user", request.message, request.user_id)
        save_chat_message(request.user_id, "assistant", complete_response, request.user_id)

    if onboarding_status == "complete":
        if data_source == "strava":
            return StreamingResponse(strava_coaching_stream(), media_type="text/event-stream")
        return StreamingResponse(coaching_stream(), media_type="text/event-stream")
    return StreamingResponse(onboarding_stream(), media_type="text/event-stream")


def _generate_initial_plan(user_id: str, profile: dict) -> None:
    """Generate and save the initial training plan after onboarding completes."""
    from services.dynamodb import save_plan_day

    if get_plan_days(user_id):
        logger.info("Plan already exists for user %s — skipping generation", user_id)
        return

    data_source = profile.get("dataSource", "garmin")

    try:
        if data_source == "strava":
            import time as _time
            from agent.agent import generate_plan_strava
            from services.dynamodb import get_strava_credentials, save_strava_credentials
            from services.strava import StravaClient

            strava_creds = get_strava_credentials(user_id)
            if not strava_creds:
                logger.error("Cannot generate plan — no Strava credentials for user %s", user_id)
                return

            access_token = strava_creds["access_token"]
            if _time.time() >= strava_creds["expires_at"] - 300:
                new_tokens = StravaClient().refresh_access_token(strava_creds["refresh_token"])
                access_token = new_tokens["access_token"]
                save_strava_credentials(
                    user_id=user_id,
                    athlete_id=strava_creds["athlete_id"],
                    access_token=access_token,
                    refresh_token=strava_creds["refresh_token"],
                    expires_at=new_tokens["expires_at"],
                )

            days = generate_plan_strava(
                user_id=user_id,
                access_token=access_token,
                athlete_id=strava_creds["athlete_id"],
                user_profile=profile,
            )
        else:
            from agent.agent import generate_plan
            from services.dynamodb import get_credentials
            from services.garmin import GarminClient
            from services.kms import decrypt_password

            credentials = get_credentials(user_id)
            if not credentials:
                logger.error("Cannot generate plan — no Garmin credentials for user %s", user_id)
                return
            plaintext_password = decrypt_password(credentials["garminPasswordEncrypted"])
            garmin_client = GarminClient()
            if not garmin_client.connect(credentials["garminEmail"], plaintext_password, user_id=user_id):
                logger.error("Cannot generate plan — Garmin auth failed for user %s", user_id)
                return
            days = generate_plan(user_id=user_id, garmin_client=garmin_client, user_profile=profile)

        for day in days:
            save_plan_day(user_id, day)
        logger.info("Initial training plan generated (%s) for user %s", data_source, user_id)
    except Exception:
        logger.exception("Failed to generate initial plan for user %s", user_id)


@router.get("/chat/history", response_model=ChatHistoryResponse)
def chat_history(user_id: str, limit: int = 50) -> ChatHistoryResponse:
    """Return recent chat history for a user, newest first."""
    messages = get_chat_history(user_id, limit=limit)
    return ChatHistoryResponse(
        messages=[
            ChatMessage(
                role=m["role"],
                message=m["message"],
                timestamp=m["timestamp"],
            )
            for m in messages
        ]
    )
