"""Strava API client for fetching athlete activity data via OAuth2."""
import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.strava.com/api/v3"
_TOKEN_URL = "https://www.strava.com/oauth/token"


def _fmt_pace(speed_mps: float | None) -> str | None:
    """Convert speed in m/s to pace string 'M:SS' per km. Returns None if invalid."""
    if not speed_mps or speed_mps <= 0:
        return None
    seconds_per_km = 1000 / speed_mps
    minutes = int(seconds_per_km // 60)
    seconds = int(seconds_per_km % 60)
    return f"{minutes}:{seconds:02d}"


def _normalize_type(sport_type: str | None) -> str:
    """Normalize Strava sport_type to a lowercase key matching the Garmin format."""
    if not sport_type:
        return "unknown"
    mapping = {
        "Run": "running",
        "TrailRun": "trail_running",
        "Walk": "walking",
        "Hike": "hiking",
        "Ride": "cycling",
        "VirtualRide": "cycling",
        "Swim": "swimming",
    }
    return mapping.get(sport_type, sport_type.lower())


def _trim_activity(raw: dict) -> dict:
    """Trim a raw Strava activity to the same shape produced by GarminClient._trim_activity()."""
    result = {}

    if raw.get("id"):
        result["activity_id"] = str(raw["id"])

    date_val = raw.get("start_date_local") or raw.get("start_date")
    if date_val:
        result["date"] = str(date_val)[:10]

    sport_type = raw.get("sport_type") or raw.get("type")
    if sport_type:
        result["type"] = _normalize_type(sport_type)

    distance = raw.get("distance")
    if distance is not None:
        result["distance_km"] = round(float(distance) / 1000, 1)

    pace = _fmt_pace(raw.get("average_speed"))
    if pace:
        result["avg_pace_per_km"] = pace

    avg_hr = raw.get("average_heartrate")
    if avg_hr is not None:
        result["avg_hr"] = int(avg_hr)

    max_hr = raw.get("max_heartrate")
    if max_hr is not None:
        result["max_hr"] = int(max_hr)

    elapsed = raw.get("elapsed_time")
    if elapsed is not None:
        result["elapsed_time_min"] = int(float(elapsed) / 60)

    elevation = raw.get("total_elevation_gain")
    if elevation is not None:
        result["elevation_gain_m"] = int(elevation)

    splits = raw.get("splits_metric") or []
    if splits:
        laps = []
        for i, split in enumerate(splits):
            lap: dict[str, Any] = {"lap": i + 1}
            split_dist = split.get("distance")
            if split_dist is not None:
                lap["distance_km"] = round(float(split_dist) / 1000, 2)
            split_pace = _fmt_pace(split.get("average_speed"))
            if split_pace:
                lap["pace_per_km"] = split_pace
            split_hr = split.get("average_heartrate")
            if split_hr is not None:
                lap["avg_hr"] = int(split_hr)
            if len(lap) > 1:
                laps.append(lap)
        if laps:
            result["laps"] = laps

    return result


class StravaClient:
    """Client for the Strava API v3."""

    def __init__(self) -> None:
        self._client_id = os.environ.get("STRAVA_CLIENT_ID", "")
        self._client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")

    def exchange_code(self, code: str, redirect_uri: str = "") -> dict:
        """
        Exchange a Strava OAuth authorization code for access and refresh tokens.

        Args:
            code: The authorization code received from Strava's OAuth redirect.

        Returns:
            Dict with access_token, refresh_token, expires_at (Unix timestamp),
            athlete_id, and athlete_name.

        Raises:
            RuntimeError: If the exchange fails.
        """
        try:
            payload: dict = {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "grant_type": "authorization_code",
            }
            if redirect_uri:
                payload["redirect_uri"] = redirect_uri
            resp = requests.post(_TOKEN_URL, data=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            athlete = data.get("athlete", {})
            first = athlete.get("firstname", "")
            last = athlete.get("lastname", "")
            return {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_at": int(data["expires_at"]),
                "athlete_id": str(athlete.get("id", "")),
                "athlete_name": f"{first} {last}".strip() or "Athlete",
            }
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else "no body"
            logger.error("Strava code exchange failed: %s — response body: %s", e, body)
            raise RuntimeError(f"Strava authentication failed: {e} — {body}") from e
        except Exception as e:
            logger.error("Strava code exchange failed: %s", e)
            raise RuntimeError(f"Strava authentication failed: {e}") from e

    def refresh_access_token(self, refresh_token: str) -> dict:
        """
        Refresh an expired Strava access token.

        Args:
            refresh_token: The long-lived refresh token.

        Returns:
            Dict with access_token and expires_at (Unix timestamp).

        Raises:
            RuntimeError: If the refresh fails.
        """
        try:
            resp = requests.post(
                _TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "access_token": data["access_token"],
                "expires_at": int(data["expires_at"]),
            }
        except Exception as e:
            logger.error("Strava token refresh failed: %s", e)
            raise RuntimeError(f"Strava token refresh failed: {e}") from e

    def get_recent_activities(self, access_token: str, days: int = 28) -> list[dict[str, Any]]:
        """
        Fetch and trim activities from the last N days.

        Args:
            access_token: A valid Strava access token.
            days: Number of days of history to retrieve (default 28).

        Returns:
            List of trimmed activity dicts in the same format as GarminClient.get_recent_activities().
        """
        try:
            after = int(time.time()) - (days * 86400)
            headers = {"Authorization": f"Bearer {access_token}"}
            activities = []
            page = 1
            while True:
                resp = requests.get(
                    f"{_BASE_URL}/athlete/activities",
                    headers=headers,
                    params={"after": after, "per_page": 100, "page": page},
                    timeout=15,
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                activities.extend(batch)
                if len(batch) < 100:
                    break
                page += 1

            trimmed = [_trim_activity(a) for a in activities if a]
            trimmed = [a for a in trimmed if a]
            logger.info("Fetched %d Strava activities for last %d days", len(trimmed), days)
            return trimmed
        except Exception as e:
            logger.error("Failed to fetch Strava activities: %s", e)
            return []

    def get_activities_for_range(self, access_token: str, since: str, until: str) -> list[dict[str, Any]]:
        """
        Fetch and trim activities between two ISO dates (inclusive).

        Args:
            access_token: A valid Strava access token.
            since: Start date as ISO string (YYYY-MM-DD).
            until: End date as ISO string (YYYY-MM-DD).

        Returns:
            List of trimmed activity dicts.
        """
        try:
            from datetime import datetime, timezone
            after = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp())
            before = int(datetime.fromisoformat(until).replace(tzinfo=timezone.utc).timestamp()) + 86400
            headers = {"Authorization": f"Bearer {access_token}"}
            activities = []
            page = 1
            while True:
                resp = requests.get(
                    f"{_BASE_URL}/athlete/activities",
                    headers=headers,
                    params={"after": after, "before": before, "per_page": 100, "page": page},
                    timeout=15,
                )
                resp.raise_for_status()
                batch = resp.json()
                if not batch:
                    break
                activities.extend(batch)
                if len(batch) < 100:
                    break
                page += 1

            trimmed = [_trim_activity(a) for a in activities if a]
            trimmed = [a for a in trimmed if a]
            logger.info("Fetched %d Strava activities for %s to %s", len(trimmed), since, until)
            return trimmed
        except Exception as e:
            logger.error("Failed to fetch Strava activities for range %s-%s: %s", since, until, e)
            return []

    def get_athlete_stats(self, access_token: str, athlete_id: str) -> dict[str, Any]:
        """
        Fetch athlete stats for training load context.

        Args:
            access_token: A valid Strava access token.
            athlete_id: The Strava athlete ID.

        Returns:
            Dict with recent (last 4 weeks) and year-to-date running totals.
        """
        try:
            resp = requests.get(
                f"{_BASE_URL}/athletes/{athlete_id}/stats",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            result: dict[str, Any] = {}
            recent = data.get("recent_run_totals", {})
            if recent:
                result["recent_runs"] = {
                    "count": recent.get("count"),
                    "distance_km": round(float(recent.get("distance", 0)) / 1000, 1),
                    "elapsed_time_hours": round(float(recent.get("elapsed_time", 0)) / 3600, 1),
                    "elevation_gain_m": int(recent.get("elevation_gain", 0)),
                }
            ytd = data.get("ytd_run_totals", {})
            if ytd:
                result["ytd_runs"] = {
                    "count": ytd.get("count"),
                    "distance_km": round(float(ytd.get("distance", 0)) / 1000, 1),
                }
            logger.info("Fetched Strava athlete stats for athlete %s", athlete_id)
            return result
        except Exception as e:
            logger.error("Failed to fetch Strava athlete stats for %s: %s", athlete_id, e)
            return {}
