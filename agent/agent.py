"""Strands agent initialisation and execution."""
import json
import logging
import os
import re
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo

from strands import Agent
from strands.models import BedrockModel

from agent.tools import make_onboarding_tools, make_strava_tools, make_tools
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
            f"{'Athlete' if entry['role'] == 'user' else 'Coach'}: {entry['message']}"
            for entry in reversed(chat_history)
        )
        return f"{context}Previous conversation:\n{history_text}\n\nAthlete: {message}"
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
    garmin_client: GarminClient | None,
    chat_history: list[dict] | None = None,
    timezone: str = "Australia/Melbourne",
):
    """
    Async generator that streams the agent response token by token.

    Accepts garmin_client=None for offline/fallback mode — tools will serve
    cached activity data and report other metrics as unavailable.
    """
    model = _build_model()
    system = SYSTEM_PROMPT
    if garmin_client is None:
        system += (
            "\n\nNote: Garmin Connect is temporarily unavailable. "
            "The get_recent_activities tool will return cached data where available. "
            "For sleep, training load, and heart rate, acknowledge to the user that "
            "live data is unavailable right now and offer to help with what you have."
        )
    agent = Agent(
        model=model,
        system_prompt=system,
        tools=make_tools(garmin_client, timezone, user_id=user_id),
    )
    prompt = _build_prompt(message, chat_history, timezone)
    logger.info("Streaming agent for user %s (garmin_online=%s)", user_id, garmin_client is not None)

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



async def stream_strava_agent(
    message: str,
    user_id: str,
    access_token: str,
    athlete_id: str,
    chat_history: list[dict] | None = None,
    timezone: str = "Australia/Melbourne",
):
    """
    Async generator that streams the coaching agent response for a Strava user.

    Uses Strava tools instead of Garmin tools. The system prompt includes a
    note that sleep and resting HR are unavailable via Strava.
    """
    from services.strava import StravaClient

    model = _build_model()
    system = COACHING_PROMPT + (
        "\n\nNote: This athlete connects via Strava, not Garmin. "
        "Sleep data and resting heart rate are not available — Strava does not provide these metrics. "
        "If asked about sleep or HR trends, acknowledge the limitation clearly and coach based on "
        "activity data and training volume instead."
    )
    agent = Agent(
        model=model,
        system_prompt=system,
        tools=make_strava_tools(StravaClient(), access_token, athlete_id, timezone, user_id),
    )
    prompt = _build_prompt(message, chat_history, timezone)
    logger.info("Streaming Strava agent for user %s", user_id)

    async for event in agent.stream_async(prompt):
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]
        elif "contentBlockDelta" in event:
            chunk = event["contentBlockDelta"].get("delta", {}).get("text", "")
            if chunk:
                yield chunk


PLAN_SYSTEM_PROMPT = """You are an expert running coach generating a complete multi-week training block.

You have access to tools to retrieve the user's Garmin data (recent activities, sleep, training load, heart rate).
Use this data to assess their current fitness before generating the plan.

You MUST respond with ONLY a flat JSON array — one object per day for the entire plan.
No markdown, no explanation, no code fences. Just the raw JSON array.

Each object must have these exact fields:
- "date": ISO date string "YYYY-MM-DD"
- "week_start": ISO date string of the Monday that week starts on "YYYY-MM-DD"
- "type": one of: intervals, tempo, threshold, fartlek, easy, long, rest
- "distance": number in kilometres (0 for rest days)
- "description": specific workout description (e.g. "10 min WU, 6 × 1km @ 4:00/km with 90s rest, 10 min CD")

Plan structure guidelines (adapt based on goal and athlete fitness):
- Base phase (first ~30% of weeks): easy mileage only, build aerobic base, no hard sessions
- Build phase (middle ~40%): introduce tempo runs and intervals, progressive weekly mileage
- Peak phase (next ~20%): highest mileage weeks, race-pace work, longest long run
- Taper (final 1–2 weeks): cut volume by 30–40%, keep some intensity, arrive fresh at race day
- For "Just run consistently": 8-week base building block, no taper needed

Rest day placement: respect the user's days-per-week constraint exactly.
Long runs always on Sunday. Hard sessions (intervals, tempo) never on consecutive days."""


# Default plan lengths in weeks by goal
_PLAN_WEEKS: dict[str, int] = {
    "First 5K": 8,
    "First 10K": 10,
    "First half marathon": 12,
    "First marathon": 18,
    "Just run consistently": 8,
}


def _plan_start() -> date:
    """Return the next Monday (always start plans on a Monday)."""
    today = date.today()
    days_until_monday = (7 - today.weekday()) % 7 or 7
    return today + timedelta(days=days_until_monday)


def _plan_weeks(goal: str, race_date_str: str | None) -> int:
    """
    Calculate how many weeks the plan should cover.

    If a race date is provided, use the number of weeks from next Monday to the
    race date, capped at the goal's default. Otherwise use the goal default.
    """
    default = _PLAN_WEEKS.get(goal, 8)
    if not race_date_str or goal == "Just run consistently":
        return default
    try:
        race_date = date.fromisoformat(race_date_str)
        start = _plan_start()
        weeks_to_race = max(1, (race_date - start).days // 7)
        return min(weeks_to_race, default)
    except (ValueError, TypeError):
        return default


def _run_plan_agent(user_id: str, user_profile: dict, tools: list) -> list[dict]:
    """
    Core plan generation logic shared by Garmin and Strava variants.
    Builds the prompt, runs the agent, parses and validates the JSON response.
    """
    goal = user_profile.get("goal", "Just run consistently")
    race_date_str = user_profile.get("targetRaceDate")
    n_weeks = _plan_weeks(goal, race_date_str)
    plan_start = _plan_start()
    plan_end = plan_start + timedelta(weeks=n_weeks) - timedelta(days=1)
    total_days = n_weeks * 7

    model = _build_model()
    agent = Agent(model=model, system_prompt=PLAN_SYSTEM_PROMPT, tools=tools)

    prompt = (
        f"Generate a complete {n_weeks}-week training block ({total_days} days total).\n\n"
        f"Plan dates: {plan_start.isoformat()} to {plan_end.isoformat()}\n"
        f"The array must contain exactly {total_days} objects — every day from {plan_start.isoformat()} to {plan_end.isoformat()} inclusive.\n\n"
        f"User profile:\n"
        f"- Name: {user_profile.get('name', 'Athlete')}\n"
        f"- Goal: {goal}\n"
        f"- Target race date: {race_date_str or 'none'}\n"
        f"- Training days per week: {user_profile.get('daysPerWeek', '4')}\n\n"
        f"First, use the available tools to check the athlete's recent activities, sleep, "
        f"training load, and heart rate. Use this to set the starting mileage appropriately.\n\n"
        f"Then respond with ONLY the raw JSON array of {total_days} objects — no markdown, no explanation."
    )

    logger.info("Generating %d-week plan for user %s (%s, %d days)", n_weeks, user_id, goal, total_days)
    result = agent(prompt)
    raw = re.sub(r"<thinking>.*?</thinking>\n?", "", str(result), flags=re.DOTALL).strip()

    match = re.search(r"\[.*\]", raw, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Agent did not return a JSON array. Response: {raw[:500]}")

    try:
        days = json.loads(match.group())
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse plan JSON: {e}. Response: {raw[:500]}")

    if not isinstance(days, list) or len(days) != total_days:
        raise ValueError(
            f"Expected {total_days} plan days, got {len(days) if isinstance(days, list) else 'non-list'}"
        )

    return days


def generate_plan(
    user_id: str,
    garmin_client: GarminClient,
    user_profile: dict,
) -> list[dict]:
    """Generate a training block using Garmin data to calibrate starting mileage."""
    return _run_plan_agent(user_id, user_profile, make_tools(garmin_client, user_id=user_id))


def generate_plan_strava(
    user_id: str,
    access_token: str,
    athlete_id: str,
    user_profile: dict,
) -> list[dict]:
    """Generate a training block using Strava data to calibrate starting mileage."""
    from services.strava import StravaClient
    tools = make_strava_tools(StravaClient(), access_token, athlete_id, user_id=user_id)
    return _run_plan_agent(user_id, user_profile, tools)
