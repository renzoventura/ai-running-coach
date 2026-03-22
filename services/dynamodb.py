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


def save_user_profile(
    user_id: str,
    goal_race: str,
    target_time: str,
    training_days: int,
) -> bool:
    """
    Save or update a user's profile in DynamoDB.

    Args:
        user_id: The unique identifier for the user.
        goal_race: The user's goal race (e.g. "Sydney Marathon 2026").
        target_time: Target finish time (e.g. "3:45:00").
        training_days: Number of days per week the user trains.

    Returns:
        True if saved successfully, False otherwise.
    """
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


def get_user_profile(user_id: str) -> Optional[dict]:
    """
    Retrieve a user's profile from DynamoDB.

    Args:
        user_id: The unique identifier for the user.

    Returns:
        Dict with goalRace, targetTime, trainingDays, createdAt, or None if not found.
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
        return {
            "goalRace": item["goalRace"],
            "targetTime": item["targetTime"],
            "trainingDays": item["trainingDays"],
            "createdAt": item["createdAt"],
        }
    except Exception as e:
        logger.error("Failed to retrieve profile for user %s: %s", user_id, e)
        return None


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


def delete_user_data(user_id: str) -> bool:
    """
    Delete all DynamoDB data for a user — profile, credentials, chat history, and training plan.

    Returns True if successful, False otherwise.
    """
    try:
        _delete_item(user_id, "PROFILE")
        _delete_item(user_id, "CREDENTIALS")
        chat_count = _delete_items_with_prefix(user_id, "CHAT#")
        plan_count = _delete_items_with_prefix(user_id, "PLAN#")
        logger.info(
            "Deleted all data for user %s — %d chat messages, %d plan days",
            user_id, chat_count, plan_count,
        )
        return True
    except Exception as e:
        logger.error("Failed to delete data for user %s: %s", user_id, e)
        return False


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
