"""Strands agent initialisation and execution."""
import json
import logging
import os
import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from strands import Agent
from strands.models import BedrockModel

from agent.tools import make_tools
from services.garmin import GarminClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a running coach. Talk like a real coach would — direct, human, and to the point.

Keep responses short. Use markdown freely — bold key stats, bullet points for lists, headers if breaking down a topic. But don't overdo it, keep it conversational.

STRICT DATA RULES — follow these without exception:
- Always call the relevant tools before answering anything about training, runs, sleep, or health.
- Only report numbers and activities that are explicitly present in the tool response. If a field is not in the data, do not mention it.
- Never estimate, infer, approximate, or invent any stat — not distance, pace, HR, load, activity type, or anything else.
- If the tool returns no data or incomplete data, say exactly that. Do not fill the gap.
- If you are not certain a value came from the tool response, do not say it.

When asked about a workout or training period:
1. Show the exact data you retrieved — only what the tool actually returned.
2. Give your coaching take based solely on what you just showed.

If you catch yourself about to say something you didn't see in the tool output, stop and don't say it."""


def run_agent(
    message: str,
    user_id: str,
    garmin_client: GarminClient,
    chat_history: list[dict] | None = None,
    timezone: str = "Australia/Melbourne",
) -> str:
    """
    Initialise a Strands agent and run it with the given user message.

    Args:
        message: The user's message or question for the coaching agent.
        user_id: The unique identifier for the user.
        garmin_client: An authenticated GarminClient instance for data retrieval.
        chat_history: Recent conversation history to prepend for context.

    Returns:
        The agent's response as a string.
    """
    model = _build_model()
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=make_tools(garmin_client),
    )
    prompt = _build_prompt(message, chat_history, timezone)
    logger.info("Running agent for user %s", user_id)
    result = agent(prompt)
    return _strip_thinking(str(result))


def _build_model() -> BedrockModel:
    return BedrockModel(
        model_id=os.environ.get("MODEL_ID", "au.anthropic.claude-haiku-4-5-20251001-v1:0"),
        region_name=os.environ.get("AWS_REGION", "ap-southeast-2"),
    )


def _build_prompt(
    message: str,
    chat_history: list[dict] | None,
    timezone: str,
) -> str:
    from datetime import datetime
    try:
        local_tz = ZoneInfo(timezone)
    except Exception:
        local_tz = ZoneInfo("Australia/Melbourne")
    now_local = datetime.now(local_tz)
    today_local = now_local.strftime("%A, %d %B %Y")

    # Pre-compute the UTC date(s) each local day of this week maps to.
    # A local day can span two UTC dates (e.g. Melbourne UTC+11 means midnight local = 1pm previous day UTC).
    monday = now_local.date() - timedelta(days=now_local.weekday())
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_map_lines = []
    utc_tz = ZoneInfo("UTC")
    for i, day_name in enumerate(day_names):
        local_date = monday + timedelta(days=i)
        start_utc = datetime(local_date.year, local_date.month, local_date.day, 0, 0, 0, tzinfo=local_tz).astimezone(utc_tz).date()
        end_utc = datetime(local_date.year, local_date.month, local_date.day, 23, 59, 59, tzinfo=local_tz).astimezone(utc_tz).date()
        utc_dates = sorted({str(start_utc), str(end_utc)})
        day_map_lines.append(f"  {day_name} {local_date} → UTC {' and '.join(utc_dates)}")

    day_map = "\n".join(day_map_lines)
    context = (
        f"Today is {today_local} ({timezone}).\n"
        f"This week's local days mapped to their UTC equivalents in Garmin:\n{day_map}\n"
        f"When the user refers to a day, use the UTC dates from the table above to find their activities. "
        f"Do not calculate timezone offsets yourself — use this table.\n\n"
    )
    if chat_history:
        history_text = "\n".join(
            f"{entry['role'].capitalize()}: {entry['message']}"
            for entry in reversed(chat_history)
        )
        return f"{context}Previous conversation:\n{history_text}\n\nUser: {message}"
    return context + message


def _strip_thinking(text: str) -> str:
    return re.sub(r"<thinking>.*?</thinking>\n?", "", text, flags=re.DOTALL).strip()


async def stream_agent(
    message: str,
    user_id: str,
    garmin_client: GarminClient,
    chat_history: list[dict] | None = None,
    timezone: str = "Australia/Melbourne",
):
    """
    Async generator that streams the agent response token by token.

    Yields plain text chunks as they arrive from the model.
    Tool calls (Garmin data fetching) happen silently before streaming begins.
    """
    model = _build_model()
    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=make_tools(garmin_client),
    )
    prompt = _build_prompt(message, chat_history, timezone)
    logger.info("Streaming agent for user %s", user_id)

    full_response = []
    async for event in agent.stream_async(prompt):
        # Text delta events
        if "data" in event and isinstance(event["data"], str):
            chunk = event["data"]
            full_response.append(chunk)
            yield chunk
        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            chunk = delta.get("text", "")
            if chunk:
                full_response.append(chunk)
                yield chunk



PLAN_SYSTEM_PROMPT = """You are an expert AI running coach generating a structured weekly training plan.

You have access to tools to retrieve the user's Garmin data (recent activities, sleep, training load, heart rate).
Use this data to tailor the plan to the user's current fitness and recovery state.

You MUST respond with ONLY a JSON array of exactly 7 objects — one per day starting from the given Monday.
No markdown, no explanation, no code fences. Just the raw JSON array.

Each object must have these exact fields:
- "date": ISO date string "YYYY-MM-DD"
- "week_start": ISO date string of the Monday for this week "YYYY-MM-DD"
- "type": one of: intervals, tempo, threshold, fartlek, easy, long, rest
- "distance": number in kilometres (0 for rest days)
- "description": detailed workout description (e.g. "10 min WU, 6 × 1km @ 4:00/km with 90s rest, 10 min CD")

Example:
[
  {"date": "2026-03-23", "week_start": "2026-03-23", "type": "easy", "distance": 8.0, "description": "Easy aerobic run at conversational pace"},
  {"date": "2026-03-24", "week_start": "2026-03-23", "type": "rest", "distance": 0, "description": "Rest day — focus on recovery and mobility"},
  ...
]"""


def generate_plan(
    user_id: str,
    garmin_client: GarminClient,
    user_profile: dict,
    week_start: date | None = None,
) -> list[dict]:
    """
    Generate a structured weekly training plan using the Strands agent.

    Args:
        user_id: The unique identifier for the user.
        garmin_client: An authenticated GarminClient instance.
        user_profile: Dict with goalRace, targetTime, trainingDays from DynamoDB.
        week_start: The Monday to start the plan week. Defaults to next Monday.

    Returns:
        List of 7 plan day dicts parsed from the agent's JSON response.

    Raises:
        ValueError: If the agent response cannot be parsed as valid plan JSON.
    """
    if week_start is None:
        today = date.today()
        # Roll forward to the next Monday (or today if already Monday)
        days_until_monday = (7 - today.weekday()) % 7 or 7
        week_start = today + timedelta(days=days_until_monday)

    model = BedrockModel(
        model_id=os.environ.get("MODEL_ID", "au.anthropic.claude-haiku-4-5-20251001-v1:0"),
        region_name=os.environ.get("AWS_REGION", "ap-southeast-2"),
    )

    agent = Agent(
        model=model,
        system_prompt=PLAN_SYSTEM_PROMPT,
        tools=make_tools(garmin_client),
    )

    prompt = (
        f"Generate a 7-day training plan starting Monday {week_start.isoformat()}.\n\n"
        f"User profile:\n"
        f"- Goal race: {user_profile.get('goalRace', 'unspecified')}\n"
        f"- Target time: {user_profile.get('targetTime', 'unspecified')}\n"
        f"- Training days per week: {user_profile.get('trainingDays', 'unspecified')}\n\n"
        f"Use the available tools to check the user's recent activities, sleep, training load, "
        f"and heart rate before generating the plan. Tailor intensity and volume accordingly.\n\n"
        f"Respond with ONLY the raw JSON array — no markdown, no explanation."
    )

    logger.info("Generating training plan for user %s (week_start=%s)", user_id, week_start)
    result = agent(prompt)
    raw = re.sub(r"<thinking>.*?</thinking>\n?", "", str(result), flags=re.DOTALL).strip()

    # Extract JSON array from the response (guard against any stray text)
    match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Agent did not return a JSON array. Response: {raw[:500]}")

    try:
        days = json.loads(match.group())
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse plan JSON: {e}. Response: {raw[:500]}")

    if not isinstance(days, list) or len(days) != 7:
        raise ValueError(f"Expected 7 plan days, got {len(days) if isinstance(days, list) else 'non-list'}")

    return days
