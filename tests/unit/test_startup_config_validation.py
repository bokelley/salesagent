"""Startup-time configuration validation contract.

``validate_configuration()`` is called from :func:`initialize_application`
before any request handler runs. It must fail loud on missing operational
secrets so operators see the problem at boot rather than in a Pydantic
serialization cascade four layers deep on the first provision request.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.core.config import validate_configuration


class TestEncryptionKeyStartupCheck:
    """ENCRYPTION_KEY is required: without it, ``decrypt_api_key()`` raises
    deep inside Pydantic field validators on the first request that touches
    an encrypted column, surfacing as an opaque 500. The startup check
    converts that into a fail-fast at boot."""

    def test_missing_encryption_key_raises_at_startup(self):
        env = {k: v for k, v in os.environ.items() if k != "ENCRYPTION_KEY"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                validate_configuration()
        assert "ENCRYPTION_KEY" in str(exc_info.value)

    def test_present_encryption_key_passes(self):
        # A syntactically-valid Fernet key — content doesn't matter for the
        # startup check, only presence does. ``clear=True`` for consistency
        # with the other tests in this class — the validation only reads
        # ENCRYPTION_KEY, not the broader environment, so isolation is safe.
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "PEg0SNGQyvzi4Nft-ForSzK8AGXyhRtql1MgoUsfUHk="}, clear=True):
            validate_configuration()  # must not raise

    def test_empty_encryption_key_raises(self):
        """Empty-string env var is the most common misconfiguration mode
        (``ENCRYPTION_KEY=`` in a partially-populated ``.env``). It must
        be treated the same as missing."""
        with patch.dict(os.environ, {"ENCRYPTION_KEY": ""}, clear=True):
            with pytest.raises(RuntimeError) as exc_info:
                validate_configuration()
        assert "ENCRYPTION_KEY" in str(exc_info.value)
