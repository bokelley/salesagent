"""Shared Fernet-encryption helpers for adapter connection schemas.

Every ad-server adapter that stores secrets (passwords, API tokens, OAuth
refresh tokens) in its connection config uses the same Pydantic pattern:

  * ``@field_serializer`` encrypts the value with Fernet before dump
  * ``@field_validator(mode="after")`` decrypts on load
  * both no-op on empty values and skip double-encryption of already-encrypted ciphertext

Rather than copy the four-method dance into every adapter's ``schemas.py``
(which trips the DRY-invariant guard), adapters call ``encrypted_secret()``
to install a matched encrypt/decrypt pair on a single Pydantic field, or
use the two raw helpers ``encrypt_secret_value`` / ``decrypt_secret_value``
when they need to wire the validators by hand.
"""

from __future__ import annotations

from src.core.utils.encryption import decrypt_api_key, encrypt_api_key, is_encrypted


def encrypt_secret_value(value: str | None) -> str | None:
    """Encrypt a plaintext secret with Fernet, idempotent on already-encrypted input.

    Returns ``value`` unchanged when it's None/empty or already ciphertext;
    otherwise returns the Fernet-encrypted form. Safe to call on every dump
    cycle without double-encrypting.
    """
    if value is None or value == "":
        return value
    return value if is_encrypted(value) else encrypt_api_key(value)


def decrypt_secret_value(value: str | None) -> str | None:
    """Decrypt a Fernet ciphertext, idempotent on plaintext input.

    Returns ``value`` unchanged when it's None/empty or not ciphertext;
    otherwise returns the decrypted plaintext. Safe to call on values that
    may or may not have been encrypted previously.
    """
    if value is None or value == "":
        return value
    return decrypt_api_key(value) if is_encrypted(value) else value
