"""Garmin Connect client wrapper."""
import logging
import statistics
from datetime import date, timedelta
from typing import Optional

import garminconnect

logger = logging.getLogger(__name__)


class GarminClient:
    """Wrapper around the garminconnect library for fetching user fitness data."""

    def __init__(self) -> None:
        self._client: Optional[garminconnect.Garmin] = None

    def _is_connected(self) -> bool:
        """Return True if connect() has been successfully called."""
        return self._client is not None

    def connect(self, email: str, password: str) -> bool:
        """
        Initialise and authenticate the Garmin Connect client.

        Must be called before any data method. Never logs credentials.

        Args:
            email: Garmin account email address.
            password: Garmin account password (plaintext, decrypted by caller).

        Returns:
            True if authentication succeeded, False otherwise.
        """
        try:
            client = garminconnect.Garmin(email, password)
            client.login()
            self._client = client
            logger.info("Garmin client connected successfully")
            return True
        except Exception as e:
            logger.error("Failed to connect to Garmin Connect: %s", e)
            return False

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
