"""Strands agent tools for retrieving Garmin data."""
import logging
from typing import Any

from strands import tool

from services.garmin import GarminClient

logger = logging.getLogger(__name__)


def make_tools(garmin_client: GarminClient) -> list:
    """
    Create Strands-compatible tool functions bound to a connected GarminClient.

    Args:
        garmin_client: An authenticated GarminClient instance.

    Returns:
        List of tool functions ready to pass to a Strands Agent.
    """

    @tool
    def get_recent_activities() -> list[dict[str, Any]]:
        """
        Retrieve the user's running activities from the last 28 days.

        Use this tool when the user asks about their recent runs, training history,
        pace, distance, or workout performance. Returns a list of activity records
        including date, distance, duration, average pace, and heart rate.
        """
        try:
            return garmin_client.get_recent_activities(days=28)
        except Exception as e:
            logger.error("Failed to get recent activities: %s", e)
            return []

    @tool
    def get_sleep_data() -> list[dict[str, Any]]:
        """
        Retrieve the user's sleep data for the last 7 nights.

        Use this tool when the user asks about their sleep, recovery, or fatigue.
        Returns nightly records including total sleep duration, sleep stages
        (light, deep, REM), and Garmin sleep score.
        """
        try:
            return garmin_client.get_sleep_data(days=7)
        except Exception as e:
            logger.error("Failed to get sleep data: %s", e)
            return []

    @tool
    def get_training_load() -> dict[str, Any]:
        """
        Retrieve the user's training load and recovery metrics for the last 28 days.

        Use this tool when the user asks about their training load, whether they are
        overtraining, or how their body is adapting to training. Returns metrics
        including acute load, chronic load, load ratio, and recovery status.
        """
        try:
            return garmin_client.get_training_load(days=28)
        except Exception as e:
            logger.error("Failed to get training load: %s", e)
            return {}

    @tool
    def get_heart_rate() -> dict[str, Any]:
        """
        Retrieve the user's resting heart rate and HR trends for the last 7 days.

        Use this tool when the user asks about their heart rate, cardiovascular
        fitness, or signs of fatigue or illness. Returns resting HR, 7-day average,
        and daily resting HR values.
        """
        try:
            return garmin_client.get_heart_rate(days=7)
        except Exception as e:
            logger.error("Failed to get heart rate data: %s", e)
            return {}

    return [get_recent_activities, get_sleep_data, get_training_load, get_heart_rate]
