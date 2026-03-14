"""Strands tools for the running coach agent."""
from src.agent.tools.running_tools import (
    get_recent_activities,
    get_sleep_data,
    get_training_load,
    get_heart_rate,
)


def get_tools() -> list:
    return [get_recent_activities, get_sleep_data, get_training_load, get_heart_rate]
