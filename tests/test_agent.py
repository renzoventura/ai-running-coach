"""Tests for the Strands agent runner."""
from unittest.mock import MagicMock, patch

from src.agent.runner import run_agent


@patch("src.agent.runner.BedrockModel")
@patch("src.agent.runner.Agent")
def test_run_agent_calls_model(mock_agent_cls, mock_model_cls):
    mock_agent = MagicMock()
    mock_agent.return_value = "Here is your coaching advice."
    mock_agent_cls.return_value = mock_agent

    result = run_agent("What pace should I run?", session_id="abc123")

    mock_agent_cls.assert_called_once()
    mock_agent.assert_called_once_with("What pace should I run?")
    assert result == "Here is your coaching advice."
