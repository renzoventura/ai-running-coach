"""Strands agent initialisation and execution."""
import json
import logging
import os
import re
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

from strands import Agent
from strands.models import BedrockModel

from agent.tools import make_onboarding_tools, make_tools
from services.garmin import GarminClient

logger = logging.getLogger(__name__)

COACHING_PROMPT = """You are a running coach. Talk like a real coach would — direct, human, and to the point.

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

# Keep SYSTEM_PROMPT as alias for backwards compatibility
SYSTEM_PROMPT = COACHING_PROMPT

ONBOARDING_PROMPT = """You are a running coach welcoming a new athlete. Be warm, direct, and human — like a real coach, not a chatbot.

Ask exactly 4 questions, one at a time, in this order. Never ask more than one question per message.

1. What is their name? → save as: name
2. What is their goal? → offer these options: First 5K, First 10K, First half marathon, First marathon, Just run consistently → save as: goal
3. Do they have a target race date? → skip this entirely if goal is "Just run consistently". Otherwise ask — if yes save the date as: targetRaceDate, if no just move on without saving anything
4. How many days a week can they run? → offer: 3, 4, or 5 → save as: daysPerWeek

Rules:
- After each answer, call save_profile(field, value) immediately before responding or asking the next question
- Never ask two questions in one message
- One sentence of acknowledgement between questions is fine, but keep it brief
- Never re-ask something already in the profile context below
- No emojis except one or two across the whole conversation

Once daysPerWeek is saved, call complete_onboarding() and tell the athlete in one or two warm sentences that their training plan is being built and they'll see it shortly. Do not list what you collected or explain methodology."""


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
        tools=make_tools(garmin_client, timezone, user_id=user_id),
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
    extra_context: str = "",
) -> str:
    try:
        local_tz = ZoneInfo(timezone)
    except Exception:
        local_tz = ZoneInfo("Australia/Melbourne")
    today_local = datetime.now(local_tz).strftime("%A, %d %B %Y")

    context = f"Today is {today_local}. All activity dates in the data are already in the user's local timezone.\n\n"
    if extra_context:
        context += extra_context + "\n\n"

    if chat_history:
        history_text = "\n".join(
            f"{entry['role'].capitalize()}: {entry['message']}"
            for entry in reversed(chat_history)
        )
        return f"{context}Previous conversation:\n{history_text}\n\nUser: {message}"
    return context + message


def _profile_context(profile: dict) -> str:
    """Format collected profile fields as context for the onboarding agent."""
    fields = {
        "name": "Name",
        "goal": "Goal",
        "targetRaceDate": "Target race date",
        "daysPerWeek": "Days per week",
    }
    collected = {label: profile[key] for key, label in fields.items() if key in profile and profile[key]}
    if not collected:
        return "No profile information collected yet."
    lines = "\n".join(f"- {label}: {val}" for label, val in collected.items())
    return f"Already collected from this athlete:\n{lines}"


def _strip_thinking(text: str) -> str:
    return re.sub(r"<thinking>.*?</thinking>\n?", "", text, flags=re.DOTALL).strip()


def run_onboarding_agent(
    message: str,
    user_id: str,
    profile: dict,
    chat_history: list[dict] | None = None,
    timezone: str = "Australia/Melbourne",
) -> str:
    """
    Run the onboarding agent for a user who has connected Garmin but not completed profile setup.

    The agent collects name, goal, race date, longest run, days per week, and injuries
    one question at a time, saving each answer via the save_profile tool.
    When complete, it calls complete_onboarding() to flip the status to "complete".
    """
    model = _build_model()
    agent = Agent(
        model=model,
        system_prompt=ONBOARDING_PROMPT,
        tools=make_onboarding_tools(user_id),
    )
    prompt = _build_prompt(message, chat_history, timezone, extra_context=_profile_context(profile))
    logger.info("Running onboarding agent for user %s", user_id)
    result = agent(prompt)
    return _strip_thinking(str(result))


async def stream_onboarding_agent(
    message: str,
    user_id: str,
    profile: dict,
    chat_history: list[dict] | None = None,
    timezone: str = "Australia/Melbourne",
):
    """
    Async generator that streams the onboarding agent response token by token.
    """
    model = _build_model()
    agent = Agent(
        model=model,
        system_prompt=ONBOARDING_PROMPT,
        tools=make_onboarding_tools(user_id),
    )
    prompt = _build_prompt(message, chat_history, timezone, extra_context=_profile_context(profile))
    logger.info("Streaming onboarding agent for user %s", user_id)

    async for event in agent.stream_async(prompt):
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]
        elif "contentBlockDelta" in event:
            chunk = event["contentBlockDelta"].get("delta", {}).get("text", "")
            if chunk:
                yield chunk


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
        tools=make_tools(garmin_client, timezone, user_id=user_id),
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
        tools=make_tools(garmin_client, user_id=user_id),
    )

    prompt = (
        f"Generate a 7-day training plan starting Monday {week_start.isoformat()}.\n\n"
        f"User profile:\n"
        f"- Name: {user_profile.get('name', 'unspecified')}\n"
        f"- Goal: {user_profile.get('goal', 'unspecified')}\n"
        f"- Target race date: {user_profile.get('targetRaceDate', 'none')}\n"
        f"- Training days per week: {user_profile.get('daysPerWeek', 'unspecified')}\n\n"
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
