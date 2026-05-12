"""FreeWheel Publisher API adapter.

Adapter for FreeWheel's publisher-side REST API (``api.freewheel.tv`` /
``api.stg.freewheel.tv``). Authenticates with a long-lived bearer token
(7-day TTL, no refresh — rotate by updating ``api_token`` in the adapter
config when expiry approaches).

The client surface is split:

- ``FreeWheelClient.inventory`` — v4 JSON inventory taxonomy (sites,
  sections, series, videos, etc). Read-only.
- ``FreeWheelClient.commercial`` — v3 XML commercial entities (advertisers,
  campaigns, insertion orders, placements). Reads plus verified
  ``create_campaign`` / ``delete_campaign`` writes.

See ``docs/adapters/freewheel/`` for the higher-level adapter narrative
and ``tests/fixtures/data/freewheel/`` for anonymised wire-format
ground truth captured against a real publisher's test network.
"""

from .adapter import FreeWheelAdapter
from .client import (
    FreeWheelAPIError,
    FreeWheelAuthError,
    FreeWheelClient,
    FreeWheelError,
    FreeWheelForbiddenError,
    FreeWheelNotFoundError,
    FreeWheelServerError,
    FreeWheelValidationError,
)
from .schemas import FreeWheelConnectionConfig, FreeWheelProductConfig

__all__ = [
    "FreeWheelAPIError",
    "FreeWheelAdapter",
    "FreeWheelAuthError",
    "FreeWheelClient",
    "FreeWheelConnectionConfig",
    "FreeWheelError",
    "FreeWheelForbiddenError",
    "FreeWheelNotFoundError",
    "FreeWheelProductConfig",
    "FreeWheelServerError",
    "FreeWheelValidationError",
]
