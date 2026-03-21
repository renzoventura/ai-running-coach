"""AWS KMS encryption and decryption for Garmin credentials."""
import base64
import logging
import os

import boto3

logger = logging.getLogger(__name__)


def _get_kms_client():
    return boto3.client(
        "kms",
        region_name=os.environ.get("AWS_REGION", "ap-southeast-2"),
    )


def encrypt_password(plaintext: str, kms_key_id: str) -> str:
    """
    Encrypt a plaintext password using AWS KMS.

    Args:
        plaintext: The plaintext password to encrypt.
        kms_key_id: The KMS key ID or ARN to use for encryption.

    Returns:
        Base64-encoded ciphertext string, safe for DynamoDB storage.

    Raises:
        RuntimeError: If encryption fails.
    """
    try:
        response = _get_kms_client().encrypt(
            KeyId=kms_key_id,
            Plaintext=plaintext.encode("utf-8"),
        )
        ciphertext_blob = response["CiphertextBlob"]
        return base64.b64encode(ciphertext_blob).decode("utf-8")
    except Exception as e:
        logger.error("Failed to encrypt password: %s", e)
        raise RuntimeError("Password encryption failed.") from e


def decrypt_password(ciphertext_b64: str) -> str:
    """
    Decrypt a KMS-encrypted password.

    The KMS key is inferred from the ciphertext blob — no KeyId required.

    Args:
        ciphertext_b64: Base64-encoded ciphertext string as stored in DynamoDB.

    Returns:
        Plaintext password string.

    Raises:
        RuntimeError: If decryption fails.
    """
    try:
        ciphertext_blob = base64.b64decode(ciphertext_b64)
        response = _get_kms_client().decrypt(CiphertextBlob=ciphertext_blob)
        return response["Plaintext"].decode("utf-8")
    except Exception as e:
        logger.error("Failed to decrypt password: %s", e)
        raise RuntimeError("Password decryption failed.") from e
