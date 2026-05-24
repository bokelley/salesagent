"""encrypt agent auth credentials

Revision ID: f1b2c3d4e5f6
Revises: 8d4f0b2c7e91
Create Date: 2026-05-24 00:00:00.000000

"""

from __future__ import annotations

import os
from collections.abc import Sequence

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text

from alembic import op
from src.core.utils.encryption import SECRET_CIPHERTEXT_PREFIX

revision: str = "f1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "8d4f0b2c7e91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES: tuple[str, ...] = ("creative_agents", "signals_agents")


def _fernet() -> Fernet:
    encryption_key = os.environ.get("ENCRYPTION_KEY")
    if not encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is required to migrate agent auth credentials")
    return Fernet(encryption_key.encode())


def _is_prefixed_encrypted(value: str, fernet: Fernet) -> bool:
    if not value.startswith(SECRET_CIPHERTEXT_PREFIX):
        return False
    ciphertext = value.removeprefix(SECRET_CIPHERTEXT_PREFIX)
    try:
        fernet.decrypt(ciphertext.encode())
        return True
    except InvalidToken:
        raise RuntimeError("Found encrypted agent auth credentials that cannot be decrypted with ENCRYPTION_KEY")


def _has_credentials() -> bool:
    connection = op.get_bind()
    for table in _TABLES:
        count = connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE auth_credentials IS NOT NULL AND auth_credentials != ''")
        ).scalar_one()
        if count:
            return True
    return False


def upgrade() -> None:
    """Encrypt non-empty CreativeAgent and SignalsAgent credentials in place."""
    if not _has_credentials():
        return

    fernet = _fernet()
    connection = op.get_bind()

    for table in _TABLES:
        rows = connection.execute(
            text(
                f"SELECT id, auth_credentials FROM {table} WHERE auth_credentials IS NOT NULL AND auth_credentials != ''"
            )
        )
        for row in rows:
            row_id = row[0]
            credentials = row[1]
            if _is_prefixed_encrypted(credentials, fernet):
                continue
            encrypted = f"{SECRET_CIPHERTEXT_PREFIX}{fernet.encrypt(credentials.encode()).decode()}"
            connection.execute(
                text(
                    f"UPDATE {table} SET auth_credentials = :credentials "
                    "WHERE id = :id AND auth_credentials = :original_credentials"
                ),
                {"credentials": encrypted, "id": row_id, "original_credentials": credentials},
            )


def downgrade() -> None:
    """Decrypt CreativeAgent and SignalsAgent credentials for rollback."""
    if not _has_credentials():
        return

    fernet = _fernet()
    connection = op.get_bind()

    for table in _TABLES:
        rows = connection.execute(
            text(
                f"SELECT id, auth_credentials FROM {table} WHERE auth_credentials IS NOT NULL AND auth_credentials != ''"
            )
        )
        for row in rows:
            row_id = row[0]
            credentials = row[1]
            if not _is_prefixed_encrypted(credentials, fernet):
                continue
            decrypted = fernet.decrypt(credentials.removeprefix(SECRET_CIPHERTEXT_PREFIX).encode()).decode()
            connection.execute(
                text(
                    f"UPDATE {table} SET auth_credentials = :credentials "
                    "WHERE id = :id AND auth_credentials = :original_credentials"
                ),
                {"credentials": decrypted, "id": row_id, "original_credentials": credentials},
            )
