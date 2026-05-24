"""Encryption utilities for sensitive data."""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

SECRET_CIPHERTEXT_PREFIX = "enc:"


class EncryptionKeyMissingError(ValueError):
    """Raised when ENCRYPTION_KEY is not set.

    A typed subclass of ``ValueError`` so callers can distinguish "key
    misconfigured at the deployment level" from "value is not a Fernet token"
    (also a ``ValueError``) without depending on the string content of the
    exception message.
    """


def _get_encryption_key() -> bytes:
    """Get encryption key from environment variable.

    Returns:
        Encryption key as bytes.

    Raises:
        EncryptionKeyMissingError: If ENCRYPTION_KEY environment variable
            is not set. Subclass of ValueError so existing callers that
            catch ValueError continue to work, but new code can catch the
            typed exception specifically.
    """
    key = os.environ.get("ENCRYPTION_KEY")
    if not key:
        raise EncryptionKeyMissingError(
            "ENCRYPTION_KEY environment variable not set. "
            "Generate a key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
        )
    return key.encode()


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt API key for storage.

    Args:
        plaintext: API key in plaintext.

    Returns:
        Encrypted API key as base64-encoded string.

    Raises:
        ValueError: If ENCRYPTION_KEY is not set or plaintext is empty.
    """
    if not plaintext:
        raise ValueError("Cannot encrypt empty string")

    key = _get_encryption_key()
    fernet = Fernet(key)
    encrypted = fernet.encrypt(plaintext.encode())
    return encrypted.decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt API key for use.

    Args:
        ciphertext: Encrypted API key as base64-encoded string.

    Returns:
        Decrypted API key in plaintext.

    Raises:
        EncryptionKeyMissingError: If ENCRYPTION_KEY is not set.
        ValueError: If ciphertext is empty or decryption fails (wrong key,
            tampered token, expired TTL).
    """
    if not ciphertext:
        raise ValueError("Cannot decrypt empty string")

    # Let EncryptionKeyMissingError propagate unchanged so callers can
    # catch it specifically. Don't wrap it in a generic ValueError.
    key = _get_encryption_key()
    fernet = Fernet(key)
    try:
        decrypted = fernet.decrypt(ciphertext.encode())
        return decrypted.decode()
    except InvalidToken:
        logger.error("Failed to decrypt API key - invalid token or wrong encryption key")
        raise ValueError("Invalid encrypted data or wrong encryption key")
    except Exception as e:
        logger.error(f"Unexpected error during decryption: {e}")
        raise ValueError(f"Decryption failed: {e}")


def is_encrypted(value: str | None) -> bool:
    """Check if a value is a Fernet ciphertext we can decrypt.

    Two-stage:

    1. **Structural check.** Fernet tokens are URL-safe-base64 of a fixed-format
       payload that begins with version byte ``0x80`` — the resulting base64
       always starts with ``gAAAAA``. Plaintext credentials that happen to
       successfully decrypt-to-something would be bizarre, but the prefix check
       short-circuits before we touch the key.
    2. **Decryption check.** Only after the structural match do we attempt
       ``decrypt_api_key()``. ``InvalidToken`` (wrong key, tampered ciphertext,
       expired TTL) returns False — caller treats the value as plaintext.
       Other exceptions (e.g. ``ENCRYPTION_KEY`` unset) propagate so a
       misconfigured deployment fails loud rather than silently re-encrypting
       ciphertext as if it were plaintext on the next save.

    Args:
        value: String to check, or None.

    Returns:
        True if value is a valid Fernet ciphertext under the current key.
        False if value is plaintext / structurally not a Fernet token / token
        is invalid for the current key.

    Raises:
        ValueError: If ``ENCRYPTION_KEY`` is not set when a structural match
            triggers a decryption attempt.
    """
    if not value:
        return False
    value = value.removeprefix(SECRET_CIPHERTEXT_PREFIX)
    if not value.startswith("gAAAAA"):
        return False
    try:
        decrypt_api_key(value)
        return True
    except EncryptionKeyMissingError:
        # Typed re-raise: a misconfigured deployment must fail loud rather
        # than silently corrupting data. Caller (e.g. a Pydantic field
        # validator) sees the exception bubble up and the request fails
        # cleanly with a 500 instead of returning ciphertext-as-plaintext.
        raise
    except ValueError:
        # InvalidToken or other Fernet errors wrapped as ValueError —
        # structural prefix matched but the value isn't decryptable under
        # the current key. Treat as not-encrypted so the caller falls
        # through to plaintext handling.
        return False


def generate_encryption_key() -> str:
    """Generate a new Fernet encryption key.

    Returns:
        New encryption key as string.
    """
    return Fernet.generate_key().decode()
