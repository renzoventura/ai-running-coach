"""AWS Lambda entry point."""
import json
import logging

from src.agent.runner import run_agent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    """Main Lambda handler."""
    logger.info("Received event: %s", json.dumps(event))

    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        user_message = body.get("message", "")
        session_id = body.get("session_id")

        if not user_message:
            return _response(400, {"error": "Missing 'message' in request body"})

        result = run_agent(user_message=user_message, session_id=session_id)

        return _response(200, {"response": result})

    except Exception as e:
        logger.exception("Unhandled error")
        return _response(500, {"error": str(e)})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body),
    }
