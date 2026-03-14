"""Tests for the Lambda handler."""
import json
from unittest.mock import patch

from src.handler import lambda_handler


def _make_event(body: dict) -> dict:
    return {"body": json.dumps(body)}


@patch("src.handler.run_agent", return_value="Great question! Here's your plan...")
def test_handler_success(mock_run_agent):
    event = _make_event({"message": "How should I train for a 5K?"})
    response = lambda_handler(event, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert "response" in body
    mock_run_agent.assert_called_once_with(
        user_message="How should I train for a 5K?", session_id=None
    )


def test_handler_missing_message():
    event = _make_event({})
    response = lambda_handler(event, None)

    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert "error" in body


@patch("src.handler.run_agent", side_effect=Exception("Model unavailable"))
def test_handler_internal_error(mock_run_agent):
    event = _make_event({"message": "Hello"})
    response = lambda_handler(event, None)

    assert response["statusCode"] == 500
    body = json.loads(response["body"])
    assert "error" in body
