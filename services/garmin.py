"""Garmin Connect client wrapper."""
import logging
import statistics
from datetime import date, timedelta
from typing import Optional

import garminconnect

logger = logging.getLogger(__name__)

# Module-level in-memory session cache — survives across requests in the same
# process (uvicorn worker or warm Lambda instance). Keyed by user_id.
_session_cache: dict[str, str] = {}


class GarminClient:
    """Wrapper around the garminconnect library for fetching user fitness data."""

    def __init__(self) -> None:
        self._client: Optional[garminconnect.Garmin] = None

    def _is_connected(self) -> bool:
        """Return True if connect() has been successfully called."""
        return self._client is not None

    # Browser headers required for Garmin's /gc-api/ endpoint to return data.
    # Without sec-ch-ua and related headers, the API returns 403.
    _BROWSER_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Google Chrome";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-fetch-dest": "empty",
        "X-Requested-With": "XMLHttpRequest",
    }

    def _try_restore(self, email: str, session_data: str) -> bool:
        """
        Attempt to restore a session from serialised token data.

        Checks the JWT_WEB expiry before restoring — if expired, returns False
        so connect() falls through to a full re-login rather than silently
        returning empty data from failed API calls.

        After restoring, patches the library's internal requests session with
        browser-like headers — required for Garmin's /gc-api/ endpoint to
        return data instead of 403.

        Returns True if session was restored, False if the data is invalid/expired.
        """
        import base64
        import json as _json
        import time
        try:
            data = _json.loads(session_data)
            jwt_web = data.get("jwt_web", "")
            # Decode JWT expiry without verifying signature
            parts = jwt_web.split(".")
            if len(parts) == 3:
                payload = parts[1] + "=="  # pad base64
                claims = _json.loads(base64.urlsafe_b64decode(payload))
                exp = claims.get("exp", 0)
                if exp and time.time() > exp:
                    logger.info("Garmin JWT_WEB expired — forcing re-login")
                    return False
        except Exception:
            pass  # If we can't decode, attempt restore anyway

        try:
            client = garminconnect.Garmin(email, "")
            client.client.loads(session_data)
            client.client.cs.headers.update(self._BROWSER_HEADERS)
            # Validate the session and populate display_name (needed for HR/sleep endpoints).
            # If this raises (e.g. 401), the session is dead — fall through to full login.
            profile = client.get_user_profile()
            client.display_name = profile.get("displayName") if profile else None
            self._client = client
            logger.info("Garmin session restored from cache (display_name=%s)", client.display_name)
            return True
        except Exception as e:
            logger.warning("Garmin session restore failed (will re-login): %s", e)
            return False

    def _full_login(self, email: str, password: str) -> bool:
        """
        Perform a full Garmin Connect login with one retry on transient failures.
        Never logs credentials.

        Raises:
            PermissionError: If Garmin returns 429 (rate limited).
            ValueError: If credentials are invalid (401).
        """
        import time
        last_err = None
        for attempt in range(2):
            try:
                client = garminconnect.Garmin(email, password)
                client.login()
                client.client.cs.headers.update(self._BROWSER_HEADERS)
                self._client = client
                logger.info("Garmin full login successful (attempt %d)", attempt + 1)
                return True
            except Exception as e:
                err = str(e)
                if "429" in err:
                    logger.warning("Garmin rate limit hit during login")
                    raise PermissionError("rate_limited")
                if "401" in err:
                    logger.warning("Garmin login rejected — invalid credentials")
                    raise ValueError("invalid_credentials")
                last_err = e
                if attempt == 0:
                    logger.warning("Garmin login attempt 1 failed (%s) — retrying in 3s", e)
                    time.sleep(3)
        logger.error("Garmin full login failed after 2 attempts: %s", last_err)
        return False

    def persist_session(self, user_id: str) -> None:
        """
        Save the current garth session tokens to memory and DynamoDB caches.

        Call this after any successful Garmin API use to keep tokens fresh —
        garth may have silently refreshed the OAuth2 access token during the
        request, and we want that updated token persisted for next time.
        """
        if not self._client or not user_id:
            return
        try:
            session_data = self._client.client.dumps()
            _session_cache[user_id] = session_data
            from services.dynamodb import save_garmin_session
            save_garmin_session(user_id, session_data)
            logger.debug("Persisted refreshed Garmin session for user %s", user_id)
        except Exception as e:
            logger.warning("Failed to persist Garmin session for user %s: %s", user_id, e)

    def connect(self, email: str, password: str, user_id: str | None = None) -> bool:
        """
        Authenticate with Garmin Connect, using a cached session where possible.

        Resolution order:
        1. In-memory cache (fastest — same process/warm Lambda)
        2. DynamoDB session cache (persists across Lambda cold starts)
        3. Full login with email + password (slowest — only when cache misses)

        After a fresh login the session is saved to both caches for next time.

        Args:
            email: Garmin account email address.
            password: Garmin account password (plaintext, decrypted by caller).
            user_id: Clerk userId — used as cache key. If None, skips caching.

        Returns:
            True if authenticated successfully, False otherwise.
        """
        import time
        # 0. Check rate limit backoff — if we hit 429 recently, skip all login attempts
        if user_id:
            from services.dynamodb import get_garmin_rate_limit
            blocked_until = get_garmin_rate_limit(user_id)
            if blocked_until and time.time() < blocked_until:
                logger.warning("Garmin login blocked until %s (rate limit backoff)", blocked_until)
                raise PermissionError("rate_limited")

        # 1. In-memory cache
        if user_id and user_id in _session_cache:
            if self._try_restore(email, _session_cache[user_id]):
                return True
            # Memory cache stale — evict and try DynamoDB
            del _session_cache[user_id]

        # 2. DynamoDB cache
        if user_id:
            from services.dynamodb import get_garmin_session
            cached = get_garmin_session(user_id)
            if cached and self._try_restore(email, cached):
                _session_cache[user_id] = cached  # warm memory cache
                return True

        # 3. Full login
        try:
            if not self._full_login(email, password):
                return False
        except PermissionError:
            # 429 — record a 24h backoff so subsequent requests don't keep hammering
            if user_id:
                from services.dynamodb import save_garmin_rate_limit
                save_garmin_rate_limit(user_id, time.time() + 86400)
                logger.warning("Garmin 429 — saved 24h rate limit backoff for user %s", user_id)
            raise

        # Save fresh session to both caches
        if user_id and self._client:
            try:
                session_data = self._client.client.dumps()
                _session_cache[user_id] = session_data
                from services.dynamodb import save_garmin_session
                save_garmin_session(user_id, session_data)
            except Exception as e:
                logger.warning("Failed to cache Garmin session for user %s: %s", user_id, e)

        return True

    def get_recent_activities(self, days: int = 28) -> list:
        """
        Fetch activities from the last N days.

        Args:
            days: Number of days of history to retrieve (default 28).

        Returns:
            List of activity dicts from Garmin Connect, or empty list on failure.
        """
        if not self._is_connected():
            logger.warning("get_recent_activities called before connect()")
            return []
        try:
            end = date.today() + timedelta(days=1)
            start = end - timedelta(days=days + 1)
            activities = self._client.get_activities_by_date(
                start.isoformat(), end.isoformat()
            )
            logger.info("Fetched %d activities for last %d days", len(activities), days)
            return activities
        except Exception as e:
            logger.error("Failed to fetch activities: %s", e)
            return []

    def get_sleep_data(self, days: int = 7) -> list:
        """
        Fetch sleep data for the last N days.

        Args:
            days: Number of days of sleep data to retrieve (default 7).

        Returns:
            List of nightly sleep records from Garmin Connect, or empty list on failure.
        """
        if not self._is_connected():
            logger.warning("get_sleep_data called before connect()")
            return []
        results = []
        end = date.today()
        start = end - timedelta(days=days)
        current = start
        while current <= end:
            try:
                sleep = self._client.get_sleep_data(current.isoformat())
                if sleep:
                    results.append(sleep)
            except Exception as e:
                logger.error("Failed to fetch sleep data for %s: %s", current.isoformat(), e)
            current += timedelta(days=1)
        logger.info("Fetched sleep data for %d days", len(results))
        return results

    def get_activity_splits(self, activity_id: str) -> dict:
        """
        Fetch lap/split data for a single activity by ID.

        Args:
            activity_id: Garmin activity ID (from the activityId field).

        Returns:
            Dict containing lapDTOs list, or empty dict on failure.
        """
        if not self._is_connected():
            logger.warning("get_activity_splits called before connect()")
            return {}
        try:
            splits = self._client.get_activity_splits(str(activity_id))
            logger.info("Fetched splits for activity %s", activity_id)
            return splits if splits else {}
        except Exception as e:
            logger.error("Failed to fetch splits for activity %s: %s", activity_id, e)
            return {}

    def get_training_load(self, days: int = 28) -> dict:
        """
        Fetch training load and recovery metrics for the last N days.

        Args:
            days: Number of days to include in training load calculation (default 28).

        Returns:
            Dict of training load metrics from Garmin Connect, or empty dict on failure.
        """
        if not self._is_connected():
            logger.warning("get_training_load called before connect()")
            return {}
        try:
            start = (date.today() - timedelta(days=days)).isoformat()
            training_status = self._client.get_training_status(start)
            logger.info("Fetched training load data")
            return training_status if training_status else {}
        except Exception as e:
            logger.error("Failed to fetch training load: %s", e)
            return {}

    def get_heart_rate(self, days: int = 7) -> dict:
        """
        Fetch resting heart rate and HR trends for the last N days.

        Args:
            days: Number of days of HR data to retrieve (default 7).

        Returns:
            Dict with restingHR (most recent), sevenDayAverage, and dailyValues list.
            Returns empty dict on failure.
        """
        if not self._is_connected():
            logger.warning("get_heart_rate called before connect()")
            return {}
        daily_values = []
        end = date.today()
        start = end - timedelta(days=days)
        current = start
        while current <= end:
            try:
                rhr = self._client.get_rhr_day(current.isoformat())
                if rhr:
                    daily_values.append({"date": current.isoformat(), "restingHR": rhr})
            except Exception as e:
                logger.error("Failed to fetch HR for %s: %s", current.isoformat(), e)
            current += timedelta(days=1)

        if not daily_values:
            logger.warning("No heart rate data retrieved for last %d days", days)
            return {}

        rhr_values = [d["restingHR"] for d in daily_values if isinstance(d["restingHR"], (int, float))]
        return {
            "restingHR": daily_values[-1]["restingHR"],
            "sevenDayAverage": round(statistics.mean(rhr_values), 1) if rhr_values else None,
            "dailyValues": daily_values,
        }
