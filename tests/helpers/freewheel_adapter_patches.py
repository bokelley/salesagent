"""Shared monkeypatch helpers for FreeWheelAdapter unit tests.

The FW adapter reads from two places that normally require live wiring:

  - ``FreeWheelInventoryRepository`` (cache lookups for ad_unit_nodes,
    sites, etc.)
  - ``get_db_session()`` (the surrounding session context manager)

Unit tests want to bypass both without standing up a database. Extracted
here so we don't duplicate the same monkeypatch block in every test
module that wires the FW adapter (the duplication guard caught it).
"""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import MagicMock


def patch_freewheel_db(monkeypatch: Any, mock_repo: Any) -> None:
    """Patch ``FreeWheelInventoryRepository`` and ``get_db_session`` inside
    the FreeWheel adapter module so a test can swap in ``mock_repo`` and
    skip the real database session entirely.

    Tests typically build ``mock_repo`` with a ``list_by_type.side_effect``
    that returns canned rows for the entity_types under test.
    """
    monkeypatch.setattr(
        "src.adapters.freewheel.adapter.FreeWheelInventoryRepository",
        lambda session, tenant_id: mock_repo,
    )
    monkeypatch.setattr(
        "src.adapters.freewheel.adapter.get_db_session",
        lambda: contextlib.nullcontext(MagicMock()),
    )
