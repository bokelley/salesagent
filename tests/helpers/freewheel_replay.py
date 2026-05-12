"""Shared FreeWheel client test helpers.

Replays captured HTTP fixtures against a mocked ``requests.Session`` so
unit tests can verify client behaviour without touching the network. Used
by the inventory, commercial, and creative client tests.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock


def make_response(text: str) -> MagicMock:
    """Build a stub ``requests.Response`` from a raw body string."""
    mock = MagicMock()
    mock.status_code = 200
    mock.ok = True
    mock.content = text.encode() if text else b""
    mock.text = text
    if text:
        try:
            mock.json.return_value = json.loads(text)
        except json.JSONDecodeError:
            # Non-JSON (XML) bodies — callers only consume ``text`` in that case.
            mock.json.return_value = {}
    else:
        mock.json.return_value = {}
    return mock


def replay_session(url_to_fixture: dict[str, Path]) -> MagicMock:
    """Return a mock ``requests.Session`` that maps URLs to fixture files.

    The match is suffix-based on the path portion (query strings ignored)
    so callers can pin a fixture to ``/services/v4/sites`` and have it
    returned regardless of ``?page=N&per_page=M`` variations.
    """
    session = MagicMock()

    def fake_request(*, method, url, headers, data=None, timeout=None):
        path = url.split("?", 1)[0]
        for suffix, fixture in url_to_fixture.items():
            if path.endswith(suffix):
                return make_response(fixture.read_text() if fixture else "")
        raise AssertionError(f"No fixture mapped for {url}")

    session.request.side_effect = fake_request
    return session
