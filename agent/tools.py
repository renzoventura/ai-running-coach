"""Strands agent tools for retrieving Garmin data."""
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from strands import tool

from services.garmin import GarminClient

logger = logging.getLogger(__name__)


def _to_local_date(timestamp: str, local_tz: ZoneInfo) -> str | None:
    """
    Convert a Garmin timestamp string to a local date string (YYYY-MM-DD).

    Garmin returns startTimeGMT as UTC and startTimeLocal as the device's local time
    (which may not match the user's current timezone). We convert GMT to the user's
    actual local timezone deterministically — no LLM involvement.
    """
    if not timestamp:
        return None
    try:
        # Garmin format: "2026-03-17 08:23:45" (no timezone info)
        dt_utc = datetime.strptime(str(timestamp)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))
        return dt_utc.astimezone(local_tz).strftime("%Y-%m-%d")
    except Exception:
        return str(timestamp)[:10]


def _fmt_pace(speed_mps: float | None) -> str | None:
    """Convert speed in m/s to pace string 'M:SS' per km. Returns None if invalid."""
    if not speed_mps or speed_mps <= 0:
        return None
    seconds_per_km = 1000 / speed_mps
    minutes = int(seconds_per_km // 60)
    seconds = int(seconds_per_km % 60)
    return f"{minutes}:{seconds:02d}"


def _trim_activity(raw: dict, local_tz: ZoneInfo | None = None) -> dict:
    """Trim a raw Garmin activity dict to only the fields the agent needs."""
    result = {}

    # Always convert from GMT to user's local timezone deterministically
    gmt = raw.get("startTimeGMT")
    if gmt and local_tz:
        result["date"] = _to_local_date(gmt, local_tz)
    else:
        date_val = raw.get("startTimeLocal") or gmt
        if date_val:
            result["date"] = str(date_val)[:10]

    activity_type = raw.get("activityType", {})
    if isinstance(activity_type, dict):
        result["type"] = activity_type.get("typeKey") or activity_type.get("typeId")
    elif activity_type:
        result["type"] = str(activity_type)

    distance = raw.get("distance")
    if distance is not None:
        result["distance_km"] = round(float(distance) / 1000, 1)

    pace = _fmt_pace(raw.get("averageSpeed"))
    if pace:
        result["avg_pace_per_km"] = pace

    avg_hr = raw.get("averageHR")
    if avg_hr is not None:
        result["avg_hr"] = int(avg_hr)

    max_hr = raw.get("maxHR")
    if max_hr is not None:
        result["max_hr"] = int(max_hr)

    duration = raw.get("elapsedDuration") or raw.get("duration")
    if duration is not None:
        result["elapsed_time_min"] = int(float(duration) / 60)

    elevation = raw.get("elevationGain")
    if elevation is not None:
        result["elevation_gain_m"] = int(elevation)

    raw_laps = raw.get("lapDTOs") or raw.get("laps") or []
    if raw_laps:
        laps = []
        for i, lap in enumerate(raw_laps):
            lap_dict = {"lap": i + 1}

            distance = lap.get("distance")
            if distance is not None:
                lap_dict["distance_km"] = round(float(distance) / 1000, 2)

            lap_pace = _fmt_pace(lap.get("averageSpeed"))
            if lap_pace:
                lap_dict["pace_per_km"] = lap_pace

            lap_hr = lap.get("averageHR")
            if lap_hr is not None:
                lap_dict["avg_hr"] = int(lap_hr)

            intensity = lap.get("intensity") or lap.get("lapTrigger")
            if intensity:
                lap_dict["type"] = str(intensity).lower()

            if len(lap_dict) > 1:
                laps.append(lap_dict)
        if laps:
            result["laps"] = laps

    return result


def _trim_sleep(raw: dict) -> dict:
    """Trim a raw Garmin sleep record to only the fields the agent needs."""
    result = {}

    dto = raw.get("dailySleepDTO") or raw
    date_val = dto.get("calendarDate") or dto.get("sleepStartTimestampLocal")
    if date_val:
        result["date"] = str(date_val)[:10]

    total = dto.get("sleepTimeSeconds")
    if total is not None:
        result["total_sleep_hours"] = round(float(total) / 3600, 1)

    deep = dto.get("deepSleepSeconds")
    if deep is not None:
        result["deep_sleep_hours"] = round(float(deep) / 3600, 1)

    rem = dto.get("remSleepSeconds")
    if rem is not None:
        result["rem_sleep_hours"] = round(float(rem) / 3600, 1)

    score = dto.get("sleepScores", {})
    if isinstance(score, dict):
        overall = score.get("overall", {})
        if isinstance(overall, dict):
            val = overall.get("value")
        else:
            val = overall
        if val is not None:
            result["sleep_score"] = int(val)
    elif raw.get("sleepScore") is not None:
        result["sleep_score"] = int(raw.get("sleepScore"))

    return result


def _trim_training_load(raw: dict) -> dict:
    """Trim raw Garmin training load/status data to only the fields the agent needs."""
    result = {}

    status = raw.get("trainingStatus") or raw.get("latestTrainingStatus")
    if status:
        result["training_status"] = str(status)

    aerobic = raw.get("aerobicTrainingEffect") or raw.get("acuteLoad")
    if aerobic is not None:
        result["aerobic_load"] = round(float(aerobic), 1)

    anaerobic = raw.get("anaerobicTrainingEffect") or raw.get("chronicLoad")
    if anaerobic is not None:
        result["anaerobic_load"] = round(float(anaerobic), 1)

    recovery = raw.get("recoveryTime") or raw.get("recoveryTimeSeconds")
    if recovery is not None:
        result["recovery_time_hours"] = int(float(recovery) / 3600) if float(recovery) > 24 else int(recovery)

    return result


def _trim_hr_day(raw: dict) -> dict:
    """Trim a single day's HR record."""
    result = {}

    date_val = raw.get("date")
    if date_val:
        result["date"] = str(date_val)[:10]

    rhr = raw.get("restingHR")
    if rhr is not None:
        result["resting_hr"] = int(rhr)

    max_hr = raw.get("maxHR") or raw.get("maxHeartRate")
    if max_hr is not None:
        result["max_hr_today"] = int(max_hr)

    hrv = raw.get("hrvStatus") or raw.get("hrv_status")
    if hrv:
        result["hrv_status"] = str(hrv)

    return result


def make_tools(garmin_client: GarminClient | None, timezone: str = "Australia/Melbourne", user_id: str | None = None) -> list:
    """
    Create Strands-compatible tool functions bound to a connected GarminClient.

    Args:
        garmin_client: An authenticated GarminClient instance.
        timezone: IANA timezone string for the user. Used to convert Garmin UTC
                  timestamps to local dates deterministically before the agent sees them.

    Returns:
        List of tool functions ready to pass to a Strands Agent.
    """
    try:
        local_tz = ZoneInfo(timezone)
    except Exception:
        local_tz = ZoneInfo("Australia/Melbourne")

    garmin_available = garmin_client is not None

    @tool
    def get_recent_activities() -> list[dict[str, Any]]:
        """
        Retrieve the user's running activities from the last 28 days, including lap splits.

        Use this tool when the user asks about their recent runs, training history,
        pace, distance, workout performance, or interval/lap splits. Returns a list
        of trimmed activity records including date, type, distance, pace, heart rate,
        and per-lap splits for activities in the last 14 days.
        """
        try:
            from datetime import date, timedelta
            from services.dynamodb import get_cached_activities, save_activities

            today = date.today()
            cache_cutoff = (today - timedelta(days=28)).isoformat()

            if not garmin_available:
                # Garmin offline — serve entirely from cache
                cached = get_cached_activities(user_id, since_date=cache_cutoff) if user_id else []
                logger.info("get_recent_activities (offline): %d cached records", len(cached))
                return cached

            fresh_cutoff = (today - timedelta(days=14)).isoformat()

            # Fetch last 14 days fresh from Garmin
            raw = garmin_client.get_recent_activities(days=14)
            fresh_trimmed = []
            for activity in raw:
                if not activity:
                    continue

                activity_type = activity.get("activityType", {})
                type_key = activity_type.get("typeKey", "") if isinstance(activity_type, dict) else ""
                is_running = "running" in str(type_key).lower()

                gmt = activity.get("startTimeGMT")
                local_date = _to_local_date(gmt, local_tz) if gmt else str(activity.get("startTimeLocal", ""))[:10]

                activity_id = activity.get("activityId")
                if is_running and activity_id:
                    splits = garmin_client.get_activity_splits(activity_id)
                    lap_dtos = splits.get("lapDTOs") or splits.get("laps") or []
                    if lap_dtos:
                        activity = {**activity, "lapDTOs": lap_dtos}

                trimmed = _trim_activity(activity, local_tz)
                if trimmed:
                    if activity_id:
                        trimmed["activity_id"] = str(activity_id)
                    fresh_trimmed.append(trimmed)

            if user_id and fresh_trimmed:
                save_activities(user_id, fresh_trimmed)

            # Load older activities (14-28 days ago) from cache
            all_cached = get_cached_activities(user_id, since_date=cache_cutoff) if user_id else []
            cached = [a for a in all_cached if (a.get("date") or "") < fresh_cutoff]

            combined = cached + fresh_trimmed
            combined.sort(key=lambda a: a.get("date") or "")
            logger.info(
                "get_recent_activities: %d from cache, %d fresh, %d total",
                len(cached), len(fresh_trimmed), len(combined),
            )
            return combined
        except Exception as e:
            logger.error("Failed to get recent activities: %s", e)
            return []

    @tool
    def get_sleep_data() -> list[dict[str, Any]]:
        """
        Retrieve the user's sleep data for the last 7 nights.

        Use this tool when the user asks about their sleep, recovery, or fatigue.
        Returns nightly records including total sleep, deep sleep, REM sleep hours,
        and Garmin sleep score.
        """
        if not garmin_available:
            return [{"unavailable": "Garmin is temporarily offline. Sleep data cannot be retrieved right now."}]
        try:
            raw = garmin_client.get_sleep_data(days=7)
            trimmed = [_trim_sleep(s) for s in raw if s]
            trimmed = [s for s in trimmed if s]
            logger.info("get_sleep_data returning %d trimmed records", len(trimmed))
            return trimmed
        except Exception as e:
            logger.error("Failed to get sleep data: %s", e)
            return []

    @tool
    def get_training_load() -> dict[str, Any]:
        """
        Retrieve the user's training load and recovery metrics for the last 28 days.

        Use this tool when the user asks about their training load, whether they are
        overtraining, or how their body is adapting to training. Returns training
        status, aerobic load, anaerobic load, and recovery time.
        """
        if not garmin_available:
            return {"unavailable": "Garmin is temporarily offline. Training load data cannot be retrieved right now."}
        try:
            raw = garmin_client.get_training_load(days=28)
            trimmed = _trim_training_load(raw)
            logger.info("get_training_load returning trimmed record: %s", list(trimmed.keys()))
            return trimmed
        except Exception as e:
            logger.error("Failed to get training load: %s", e)
            return {}

    @tool
    def get_heart_rate() -> list[dict[str, Any]]:
        """
        Retrieve the user's resting heart rate and HR trends for the last 7 days.

        Use this tool when the user asks about their heart rate, cardiovascular
        fitness, or signs of fatigue or illness. Returns per-day records with
        resting HR, max HR, and HRV status.
        """
        if not garmin_available:
            return [{"unavailable": "Garmin is temporarily offline. Heart rate data cannot be retrieved right now."}]
        try:
            raw = garmin_client.get_heart_rate(days=7)
            daily = raw.get("dailyValues", [])
            trimmed = [_trim_hr_day(d) for d in daily if d]
            trimmed = [d for d in trimmed if d]
            logger.info("get_heart_rate returning %d trimmed records", len(trimmed))
            return trimmed
        except Exception as e:
            logger.error("Failed to get heart rate data: %s", e)
            return []

    return [get_recent_activities, get_sleep_data, get_training_load, get_heart_rate]


def make_onboarding_tools(user_id: str) -> list:
    """
    Create tools for the onboarding agent.

    The onboarding agent does not have access to Garmin data — it only needs
    to save profile fields progressively and signal when onboarding is complete.

    Args:
        user_id: The Clerk userId to save profile data against.

    Returns:
        List of tool functions: save_profile and complete_onboarding.
    """
    from services.dynamodb import set_onboarding_status, update_profile_field

    @tool
    def save_profile(field: str, value: str) -> str:
        """
        Save a single profile field for the user.

        Call this immediately after the user provides each piece of information.
        Do not wait until all fields are collected — save each answer as soon as you have it.

        Args:
            field: One of: name, goal, targetRaceDate, currentLongestRun, daysPerWeek, injuries
            value: The value provided by the user (as a string).

        Returns:
            Confirmation string.
        """
        allowed = {"name", "goal", "targetRaceDate", "daysPerWeek"}
        if field not in allowed:
            logger.warning("Onboarding agent tried to save unknown field: %s", field)
            return f"Unknown field '{field}'. Allowed: {', '.join(sorted(allowed))}"
        update_profile_field(user_id, field, value)
        logger.info("Onboarding saved field '%s' for user %s", field, user_id)
        return f"Saved {field}."

    @tool
    def complete_onboarding() -> str:
        """
        Call this once you have collected and saved all required profile fields:
        name, goal, targetRaceDate (if applicable), daysPerWeek.

        This marks the user's onboarding as complete and triggers training plan generation.
        Tell the user their personalised training plan is being generated.

        Returns:
            Confirmation string.
        """
        set_onboarding_status(user_id, "complete")
        logger.info("Onboarding complete for user %s", user_id)
        return "Onboarding complete. Training plan will be generated."

    return [save_profile, complete_onboarding]


def make_strava_tools(
    strava_client,
    access_token: str,
    athlete_id: str,
    timezone: str = "Australia/Melbourne",
    user_id: str | None = None,
) -> list:
    """
    Create Strands-compatible tool functions for a Strava-connected user.

    Strava does not provide sleep or resting heart rate data — those tools
    return a descriptive note so the agent can acknowledge the gap clearly.

    Args:
        strava_client: An authenticated StravaClient instance.
        access_token: Valid Strava access token.
        athlete_id: Strava athlete ID (for stats endpoint).
        timezone: IANA timezone string. Used for cache cutoff dates.
        user_id: Clerk userId — used as DynamoDB cache key.

    Returns:
        List of four tool functions ready to pass to a Strands Agent.
    """
    @tool
    def get_recent_activities() -> list[dict[str, Any]]:
        """
        Retrieve the athlete's running activities from the last 28 days.

        Use this tool when the user asks about recent runs, training history,
        pace, distance, workout performance, or splits. Returns a list of
        trimmed activity records including date, type, distance, pace, heart
        rate, and per-km splits.
        """
        try:
            from datetime import date, timedelta
            from services.dynamodb import get_cached_activities, save_activities

            today = date.today()
            cache_cutoff = (today - timedelta(days=28)).isoformat()
            fresh_cutoff = (today - timedelta(days=14)).isoformat()

            fresh = strava_client.get_recent_activities(access_token, days=14)

            if user_id and fresh:
                save_activities(user_id, fresh)

            all_cached = get_cached_activities(user_id, since_date=cache_cutoff) if user_id else []
            cached = [a for a in all_cached if (a.get("date") or "") < fresh_cutoff]

            combined = cached + fresh
            combined.sort(key=lambda a: a.get("date") or "")
            logger.info(
                "get_recent_activities (Strava): %d from cache, %d fresh, %d total",
                len(cached), len(fresh), len(combined),
            )
            return combined
        except Exception as e:
            logger.error("Failed to get Strava activities: %s", e)
            return []

    @tool
    def get_sleep_data() -> list[dict[str, Any]]:
        """
        Sleep data is not available for Strava users.

        Strava does not provide sleep tracking. If the user asks about sleep,
        acknowledge this limitation and offer coaching advice based on activity
        data alone.
        """
        return [{"note": "Sleep data is not available via Strava. Strava does not provide sleep tracking."}]

    @tool
    def get_training_load() -> dict[str, Any]:
        """
        Retrieve recent and year-to-date training volume from Strava athlete stats.

        Use this tool when the user asks about training load, weekly volume,
        total mileage, or overall training history. Returns recent (4-week)
        and year-to-date run totals including count, distance, and time.
        """
        try:
            return strava_client.get_athlete_stats(access_token, athlete_id)
        except Exception as e:
            logger.error("Failed to get Strava athlete stats: %s", e)
            return {}

    @tool
    def get_heart_rate() -> list[dict[str, Any]]:
        """
        Resting heart rate data is not available for Strava users.

        Strava does not expose resting HR or HRV data. If the user asks about
        heart rate trends or recovery, acknowledge this limitation and coach
        based on pace and perceived effort from activity data instead.
        """
        return [{"note": "Resting heart rate data is not available via Strava. Strava does not expose this metric."}]

    return [get_recent_activities, get_sleep_data, get_training_load, get_heart_rate]
