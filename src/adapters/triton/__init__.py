"""Triton Digital TAP adapter.

Adapter for Triton Digital's Media Buying API (TAP). Authenticates via
publisher-scoped JWT obtained from the Login API and creates Advertisers,
Campaigns, and Flights against ``mbapi.tritondigital.com``. Stations are a
flight-level targeting dimension, configured per-product via
``TritonProductConfig.station_ids``.
"""

from .adapter import TritonAdapter
from .client import TritonAPIError, TritonClient
from .schemas import TritonConnectionConfig, TritonProductConfig

__all__ = [
    "TritonAdapter",
    "TritonAPIError",
    "TritonClient",
    "TritonConnectionConfig",
    "TritonProductConfig",
]
