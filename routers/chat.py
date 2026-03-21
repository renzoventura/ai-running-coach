"""Chat endpoint — receives a user message and returns the agent response."""
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from agent.agent import run_agent, stream_agent
from models.schemas import ChatHistoryResponse, ChatMessage, ChatRequest, ChatResponse
from services.dynamodb import get_chat_history, get_credentials, save_chat_message
from services.garmin import GarminClient
from services.kms import decrypt_password

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """
    Run the Strands agent with the user's message and return its response.

    Fetches Garmin credentials from DynamoDB, decrypts via KMS, connects to
    Garmin, then runs the agent with recent chat history for context.
    """
    credentials = get_credentials(request.user_id)
    if not credentials:
        logger.info("No credentials found for user %s", request.user_id)
        raise HTTPException(
            status_code=404,
            detail="User credentials not found. Please complete onboarding first.",
        )

    try:
        plaintext_password = decrypt_password(credentials["garminPasswordEncrypted"])
    except RuntimeError:
        logger.error("Failed to decrypt credentials for user %s", request.user_id)
        raise HTTPException(
            status_code=503,
            detail="Unable to retrieve Garmin credentials. Please try again.",
        )

    garmin_client = GarminClient()
    connected = garmin_client.connect(credentials["garminEmail"], plaintext_password)
    if not connected:
        logger.error("Garmin Connect authentication failed for user %s", request.user_id)
        raise HTTPException(
            status_code=503,
            detail="Unable to connect to Garmin. Please check your credentials and try again.",
        )

    chat_history = get_chat_history(request.user_id, limit=20)

    try:
        response = run_agent(
            message=request.message,
            user_id=request.user_id,
            garmin_client=garmin_client,
            chat_history=chat_history,
            timezone=request.timezone,
        )
    except Exception:
        logger.exception("Agent error for user %s", request.user_id)
        raise HTTPException(status_code=500, detail="Agent error. Please try again.")

    save_chat_message(
        user_id=request.user_id,
        role="user",
        message=request.message,
        conversation_id=request.user_id,
    )
    save_chat_message(
        user_id=request.user_id,
        role="assistant",
        message=response,
        conversation_id=request.user_id,
    )

    return ChatResponse(response=response)


def _get_garmin_client(user_id: str) -> tuple[GarminClient, str]:
    """Shared helper — fetch credentials, decrypt, connect. Returns (client, email)."""
    credentials = get_credentials(user_id)
    if not credentials:
        raise HTTPException(status_code=404, detail="User credentials not found. Please complete onboarding first.")
    try:
        plaintext_password = decrypt_password(credentials["garminPasswordEncrypted"])
    except RuntimeError:
        logger.error("Failed to decrypt credentials for user %s", user_id)
        raise HTTPException(status_code=503, detail="Unable to retrieve Garmin credentials. Please try again.")
    garmin_client = GarminClient()
    if not garmin_client.connect(credentials["garminEmail"], plaintext_password):
        logger.error("Garmin Connect authentication failed for user %s", user_id)
        raise HTTPException(status_code=503, detail="Unable to connect to Garmin. Please check your credentials and try again.")
    return garmin_client, credentials["garminEmail"]


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    """
    Stream the agent response token by token using Server-Sent Events.

    The frontend should consume this with fetch + ReadableStream.
    After streaming, the full conversation is saved to DynamoDB.
    """
    garmin_client, _ = _get_garmin_client(request.user_id)
    chat_history = get_chat_history(request.user_id, limit=20)

    async def event_stream():
        full_response = []
        try:
            async for chunk in stream_agent(
                message=request.message,
                user_id=request.user_id,
                garmin_client=garmin_client,
                chat_history=chat_history,
                timezone=request.timezone,
            ):
                full_response.append(chunk)
                # SSE multi-line: split on newlines and prefix each line with "data: "
                # The SSE spec joins them back with \n on the client automatically
                sse_lines = "\n".join(f"data: {line}" for line in chunk.split("\n"))
                yield f"{sse_lines}\n\n"
        except Exception:
            logger.exception("Streaming agent error for user %s", request.user_id)
            yield "data: [ERROR]\n\n"
            return

        yield "data: [DONE]\n\n"

        # Save to DynamoDB after stream completes
        complete_response = "".join(full_response)
        save_chat_message(request.user_id, "user", request.message, request.user_id)
        save_chat_message(request.user_id, "assistant", complete_response, request.user_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
