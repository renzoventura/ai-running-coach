"""DynamoDB read/write operations."""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

_TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "ai-running-coach")


def _get_table():
    """Return a DynamoDB Table resource."""
    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-southeast-2"))
    return dynamodb.Table(_TABLE_NAME)


def create_profile(
    user_id: str,
    onboarding_status: str = "garmin_connected",
    data_source: str = "garmin",
) -> bool:
    """
    Create an initial user profile with the given onboarding status.

    Args:
        user_id: The unique identifier for the user.
        onboarding_status: Initial status — "garmin_connected" after data source is linked.
        data_source: "garmin" or "strava" — determines which tools the coaching agent uses.

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        table = _get_table()
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "PROFILE",
                "onboardingStatus": onboarding_status,
                "dataSource": data_source,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info("Created profile for user %s (status=%s, source=%s)", user_id, onboarding_status, data_source)
        return True
    except Exception as e:
        logger.error("Failed to create profile for user %s: %s", user_id, e)
        return False


def update_profile_field(user_id: str, field: str, value: str) -> bool:
    """
    Update a single field on the user's profile.

    Args:
        user_id: The unique identifier for the user.
        field: The DynamoDB attribute name to update.
        value: The new value (string).

    Returns:
        True if updated successfully, False otherwise.
    """
    try:
        table = _get_table()
        table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": "PROFILE"},
            UpdateExpression="SET #f = :v",
            ExpressionAttributeNames={"#f": field},
            ExpressionAttributeValues={":v": value},
        )
        logger.info("Updated profile field '%s' for user %s", field, user_id)
        return True
    except Exception as e:
        logger.error("Failed to update profile field '%s' for user %s: %s", field, user_id, e)
        return False


def set_onboarding_status(user_id: str, status: str) -> bool:
    """
    Set the onboardingStatus field on the user's profile.

    Args:
        user_id: The unique identifier for the user.
        status: "garmin_connected" | "complete"

    Returns:
        True if updated successfully, False otherwise.
    """
    return update_profile_field(user_id, "onboardingStatus", status)


def get_user_profile(user_id: str) -> Optional[dict]:
    """
    Retrieve a user's profile from DynamoDB.

    Returns all stored profile fields, or None if not found.
    """
    try:
        table = _get_table()
        response = table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": "PROFILE"}
        )
        item = response.get("Item")
        if not item:
            logger.info("No profile found for user %s", user_id)
            return None
        # Return all fields except DynamoDB keys
        return {k: v for k, v in item.items() if k not in ("PK", "SK")}
    except Exception as e:
        logger.error("Failed to retrieve profile for user %s: %s", user_id, e)
        return None


def save_user_profile(
    user_id: str,
    goal_race: str,
    target_time: str,
    training_days: int,
) -> bool:
    """Legacy profile save — kept for backwards compatibility."""
    try:
        table = _get_table()
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "PROFILE",
                "goalRace": goal_race,
                "targetTime": target_time,
                "trainingDays": training_days,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info("Saved profile for user %s", user_id)
        return True
    except Exception as e:
        logger.error("Failed to save profile for user %s: %s", user_id, e)
        return False


def save_credentials(
    user_id: str,
    garmin_email: str,
    garmin_password_encrypted: str,
    kms_key_id: str,
) -> bool:
    """
    Store Garmin credentials for a user in DynamoDB.

    The password must already be encrypted before being passed to this function.
    KMS encryption is handled by services/kms.py prior to calling this.

    Args:
        user_id: The unique identifier for the user.
        garmin_email: The user's Garmin account email.
        garmin_password_encrypted: KMS-encrypted Garmin password ciphertext.
        kms_key_id: The KMS key ID used to encrypt the password.

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        table = _get_table()
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "CREDENTIALS",
                "garminEmail": garmin_email,
                "garminPasswordEncrypted": garmin_password_encrypted,
                "kmsKeyId": kms_key_id,
            }
        )
        logger.info("Saved credentials for user %s", user_id)
        return True
    except Exception as e:
        logger.error("Failed to save credentials for user %s: %s", user_id, e)
        return False


def get_credentials(user_id: str) -> Optional[dict]:
    """
    Retrieve stored Garmin credentials for a user from DynamoDB.

    The returned password is still encrypted. Decryption is handled by services/kms.py.

    Args:
        user_id: The unique identifier for the user.

    Returns:
        Dict with garminEmail, garminPasswordEncrypted, kmsKeyId, or None if not found.
    """
    try:
        table = _get_table()
        response = table.get_item(
            Key={"PK": f"USER#{user_id}", "SK": "CREDENTIALS"}
        )
        item = response.get("Item")
        if not item:
            logger.info("No credentials found for user %s", user_id)
            return None
        return {
            "garminEmail": item["garminEmail"],
            "garminPasswordEncrypted": item["garminPasswordEncrypted"],
            "kmsKeyId": item["kmsKeyId"],
        }
    except Exception as e:
        logger.error("Failed to retrieve credentials for user %s: %s", user_id, e)
        return None


def save_chat_message(
    user_id: str,
    role: str,
    message: str,
    conversation_id: str,
) -> bool:
    """
    Save a single chat message to the user's chat history in DynamoDB.

    Args:
        user_id: The unique identifier for the user.
        role: Message role — "user" or "assistant".
        message: The message content.
        conversation_id: Groups messages belonging to the same conversation.

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        timestamp = datetime.now(timezone.utc).isoformat()
        table = _get_table()
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": f"CHAT#{timestamp}",
                "role": role,
                "message": message,
                "conversationId": conversation_id,
            }
        )
        logger.info("Saved chat message for user %s (role=%s)", user_id, role)
        return True
    except Exception as e:
        logger.error("Failed to save chat message for user %s: %s", user_id, e)
        return False


def save_plan_day(user_id: str, day: dict) -> bool:
    """
    Save a single training plan day for a user in DynamoDB.

    Args:
        user_id: The unique identifier for the user.
        day: Dict with date, week_start, type, distance, description.

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        table = _get_table()
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": f"PLAN#{day['date']}",
                "weekStart": day["week_start"],
                "type": day["type"],
                "distance": str(day["distance"]),
                "description": day["description"],
            }
        )
        logger.info("Saved plan day %s for user %s", day["date"], user_id)
        return True
    except Exception as e:
        logger.error("Failed to save plan day for user %s: %s", user_id, e)
        return False


def get_plan_days(user_id: str) -> list[dict]:
    """
    Retrieve all training plan days for a user, sorted by date ascending.

    Args:
        user_id: The unique identifier for the user.

    Returns:
        List of plan day dicts with date, week_start, type, distance, description.
        Returns empty list if none found or on error.
    """
    try:
        table = _get_table()
        response = table.query(
            KeyConditionExpression=(
                Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("PLAN#")
            ),
            ScanIndexForward=True,
        )
        return [
            {
                "date": item["SK"].removeprefix("PLAN#"),
                "week_start": item["weekStart"],
                "type": item["type"],
                "distance": float(item["distance"]),
                "description": item["description"],
            }
            for item in response.get("Items", [])
        ]
    except Exception as e:
        logger.error("Failed to retrieve plan days for user %s: %s", user_id, e)
        return []


def _delete_items_with_prefix(user_id: str, sk_prefix: str) -> int:
    """
    Delete all DynamoDB items for a user whose SK starts with the given prefix.

    Returns the number of items deleted.
    """
    table = _get_table()
    deleted = 0
    last_key = None
    while True:
        kwargs = {
            "KeyConditionExpression": Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with(sk_prefix),
            "ProjectionExpression": "PK, SK",
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        response = table.query(**kwargs)
        items = response.get("Items", [])
        with table.batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"PK": item["PK"], "SK": item["SK"]})
                deleted += 1
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
    return deleted


def _delete_item(user_id: str, sk: str) -> None:
    """Delete a single DynamoDB item by exact PK + SK."""
    _get_table().delete_item(Key={"PK": f"USER#{user_id}", "SK": sk})


def clear_chat_history(user_id: str) -> bool:
    """
    Delete all chat messages for a user.

    Returns True if successful, False otherwise.
    """
    try:
        count = _delete_items_with_prefix(user_id, "CHAT#")
        logger.info("Cleared %d chat messages for user %s", count, user_id)
        return True
    except Exception as e:
        logger.error("Failed to clear chat history for user %s: %s", user_id, e)
        return False


def is_month_synced(user_id: str, year_month: str) -> bool:
    """
    Return True if activities for this month have already been fetched and cached.

    Args:
        user_id: Clerk userId.
        year_month: Month in YYYY-MM format.
    """
    try:
        table = _get_table()
        result = table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"SYNC#{year_month}"})
        return "Item" in result
    except Exception as e:
        logger.warning("Failed to check sync marker for %s %s: %s", user_id, year_month, e)
        return False


def mark_month_synced(user_id: str, year_month: str) -> None:
    """
    Record that activities for this month have been fetched and cached.

    Args:
        user_id: Clerk userId.
        year_month: Month in YYYY-MM format.
    """
    try:
        table = _get_table()
        table.put_item(Item={"PK": f"USER#{user_id}", "SK": f"SYNC#{year_month}"})
    except Exception as e:
        logger.warning("Failed to set sync marker for %s %s: %s", user_id, year_month, e)


def delete_user_data(user_id: str) -> bool:
    """
    Delete all DynamoDB data for a user — profile, credentials, chat history, and training plan.

    Returns True if successful, False otherwise.
    """
    try:
        _delete_item(user_id, "PROFILE")
        _delete_item(user_id, "CREDENTIALS")
        _delete_item(user_id, "GARMIN_SESSION")
        _delete_item(user_id, "STRAVA_CREDENTIALS")
        chat_count = _delete_items_with_prefix(user_id, "CHAT#")
        plan_count = _delete_items_with_prefix(user_id, "PLAN#")
        activity_count = _delete_items_with_prefix(user_id, "ACTIVITY#")
        _delete_items_with_prefix(user_id, "SYNC#")
        logger.info(
            "Deleted all data for user %s — %d chat messages, %d plan days, %d activities",
            user_id, chat_count, plan_count, activity_count,
        )
        return True
    except Exception as e:
        logger.error("Failed to delete data for user %s: %s", user_id, e)
        return False


def save_garmin_session(user_id: str, session_data: str) -> bool:
    """
    Persist a serialised garth session string to DynamoDB.

    Args:
        user_id: The unique identifier for the user.
        session_data: Serialised garth OAuth2 token string from client.garth.dumps().

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        table = _get_table()
        table.put_item(Item={
            "PK": f"USER#{user_id}",
            "SK": "GARMIN_SESSION",
            "sessionData": session_data,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("Saved Garmin session for user %s", user_id)
        return True
    except Exception as e:
        logger.error("Failed to save Garmin session for user %s: %s", user_id, e)
        return False


def get_garmin_session(user_id: str) -> Optional[str]:
    """
    Retrieve a cached garth session string from DynamoDB.

    Args:
        user_id: The unique identifier for the user.

    Returns:
        Serialised garth session string, or None if not found.
    """
    try:
        table = _get_table()
        response = table.get_item(Key={"PK": f"USER#{user_id}", "SK": "GARMIN_SESSION"})
        item = response.get("Item")
        return item["sessionData"] if item else None
    except Exception as e:
        logger.error("Failed to retrieve Garmin session for user %s: %s", user_id, e)
        return None


def save_activities(user_id: str, activities: list[dict]) -> bool:
    """
    Upsert a list of trimmed activity dicts to DynamoDB.

    SK format: ACTIVITY#<date>#<activity_id>
    If an activity has no activity_id, falls back to date-only SK (may overwrite).

    Args:
        user_id: The unique identifier for the user.
        activities: List of trimmed activity dicts from _trim_activity().
                    Each must have at least a 'date' field.

    Returns:
        True if all saved successfully, False if any write failed.
    """
    if not activities:
        return True
    try:
        table = _get_table()
        with table.batch_writer() as batch:
            for activity in activities:
                date = activity.get("date")
                if not date:
                    continue
                activity_id = activity.get("activity_id", date)
                # Strip laps and activity_id — laps contain nested floats that DynamoDB
                # can't store natively, and activity_id is already encoded in the SK.
                # Calendar display only needs the summary fields.
                exclude = {"laps", "activity_id"}
                item = {
                    k: str(v) if isinstance(v, float) else v
                    for k, v in activity.items()
                    if k not in exclude
                }
                batch.put_item(Item={
                    "PK": f"USER#{user_id}",
                    "SK": f"ACTIVITY#{date}#{activity_id}",
                    **item,
                })
        logger.info("Saved %d activities for user %s", len(activities), user_id)
        return True
    except Exception as e:
        logger.error("Failed to save activities for user %s: %s", user_id, e)
        return False


def get_cached_activities(user_id: str, since_date: str | None = None) -> list[dict]:
    """
    Retrieve cached activity records from DynamoDB, optionally filtered by date.

    Args:
        user_id: The unique identifier for the user.
        since_date: ISO date string (YYYY-MM-DD). Only return activities on or after this date.
                    If None, returns all stored activities.

    Returns:
        List of activity dicts sorted by date ascending.
    """
    try:
        table = _get_table()
        if since_date:
            response = table.query(
                KeyConditionExpression=(
                    Key("PK").eq(f"USER#{user_id}") & Key("SK").between(
                        f"ACTIVITY#{since_date}", "ACTIVITY#{}"
                    )
                ),
                ScanIndexForward=True,
            )
        else:
            response = table.query(
                KeyConditionExpression=(
                    Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("ACTIVITY#")
                ),
                ScanIndexForward=True,
            )
        items = response.get("Items", [])
        result = []
        for item in items:
            activity = {k: v for k, v in item.items() if k not in ("PK", "SK")}
            # Restore floats that were stored as strings
            for field in ("distance_km",):
                if field in activity:
                    try:
                        activity[field] = float(activity[field])
                    except (ValueError, TypeError):
                        pass
            result.append(activity)
        return result
    except Exception as e:
        logger.error("Failed to retrieve cached activities for user %s: %s", user_id, e)
        return []


def save_strava_credentials(
    user_id: str,
    athlete_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: int,
) -> bool:
    """
    Store Strava OAuth credentials for a user in DynamoDB.

    Args:
        user_id: The unique identifier for the user.
        athlete_id: The Strava athlete ID.
        access_token: Short-lived Strava access token (valid ~6 hours).
        refresh_token: Long-lived token used to get new access tokens.
        expires_at: Unix timestamp when access_token expires.

    Returns:
        True if saved successfully, False otherwise.
    """
    try:
        table = _get_table()
        table.put_item(
            Item={
                "PK": f"USER#{user_id}",
                "SK": "STRAVA_CREDENTIALS",
                "athleteId": athlete_id,
                "accessToken": access_token,
                "refreshToken": refresh_token,
                "expiresAt": expires_at,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info("Saved Strava credentials for user %s", user_id)
        return True
    except Exception as e:
        logger.error("Failed to save Strava credentials for user %s: %s", user_id, e)
        return False


def get_strava_credentials(user_id: str) -> Optional[dict]:
    """
    Retrieve stored Strava credentials for a user from DynamoDB.

    Args:
        user_id: The unique identifier for the user.

    Returns:
        Dict with athlete_id, access_token, refresh_token, expires_at, or None if not found.
    """
    try:
        table = _get_table()
        response = table.get_item(Key={"PK": f"USER#{user_id}", "SK": "STRAVA_CREDENTIALS"})
        item = response.get("Item")
        if not item:
            logger.info("No Strava credentials found for user %s", user_id)
            return None
        return {
            "athlete_id": item["athleteId"],
            "access_token": item["accessToken"],
            "refresh_token": item["refreshToken"],
            "expires_at": int(item["expiresAt"]),
        }
    except Exception as e:
        logger.error("Failed to retrieve Strava credentials for user %s: %s", user_id, e)
        return None


def get_chat_history(user_id: str, limit: int = 20) -> list[dict]:
    """
    Retrieve the most recent chat messages for a user, sorted newest first.

    Args:
        user_id: The unique identifier for the user.
        limit: Maximum number of messages to return (default 20).

    Returns:
        List of message dicts with role, message, conversationId, and timestamp.
        Returns an empty list if no history found or on error.
    """
    try:
        table = _get_table()
        response = table.query(
            KeyConditionExpression=(
                Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("CHAT#")
            ),
            ScanIndexForward=False,
            Limit=limit,
        )
        items = response.get("Items", [])
        return [
            {
                "role": item["role"],
                "message": item["message"],
                "conversationId": item["conversationId"],
                "timestamp": item["SK"].removeprefix("CHAT#"),
            }
            for item in items
        ]
    except Exception as e:
        logger.error("Failed to retrieve chat history for user %s: %s", user_id, e)
        return []
