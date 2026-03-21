"""Strands agent setup and execution."""
import logging
import os
from typing import Optional

from strands import Agent
from strands.models import BedrockModel

from src.agent.tools import get_tools
from src.agent.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def run_agent(user_message: str, session_id: Optional[str] = None) -> str:
    """Run the Strands agent with the given user message."""
    model = BedrockModel(
        model_id=os.environ.get("MODEL_ID", "anthropic.claude-haiku-3-5-20241022-v2:0"),
        region_name=os.environ.get("AWS_REGION", "ap-southeast-1"),
    )

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=get_tools(),
    )

    logger.info("Running agent for session: %s", session_id)
    result = agent(user_message)

    return str(result)
