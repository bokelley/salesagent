"""FreeWheel Publisher API adapter.

Adapter for Comcast/FreeWheel's publisher-side REST API
(``api.freewheel.tv`` / ``api.stg.freewheel.tv``). Authenticates via OAuth2
``client_credentials`` and creates Campaigns, Line Items, and Creatives
(with Creative-LineItem associations) on behalf of advertisers.

Skeleton-only as of this commit: live-mode operations stub out with
clear pending-credentials errors. Once FreeWheel staging credentials
land we wire create/update/reporting against the real endpoints.
"""

from .adapter import FreeWheelAdapter
from .client import FreeWheelAPIError, FreeWheelClient
from .schemas import FreeWheelConnectionConfig, FreeWheelProductConfig

__all__ = [
    "FreeWheelAPIError",
    "FreeWheelAdapter",
    "FreeWheelClient",
    "FreeWheelConnectionConfig",
    "FreeWheelProductConfig",
]
