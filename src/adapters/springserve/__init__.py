"""SpringServe (Magnite) ad-server adapter.

Adapter for SpringServe's REST API at ``console.springserve.com/api/v0``.
Authenticates with an email + password credential pair (the API mints a
2-hour token at ``POST /api/v0/auth``) or with a pre-minted API token.

Entity mapping (Mapping A -- see docs/adapters/springserve/):

- AdCP MediaBuy -> SpringServe Campaign
- AdCP Package  -> SpringServe Demand Tag
- AdCP Creative -> SpringServe Video/Audio Creative or VAST URL on the demand tag

Stage 1 ships skeleton + auth + dry-run. Live writes (Campaign + Demand Tag
create, creative upload + bind, reporting cache, inventory sync) land in
Stages 2-5 per ``.context/springserve-adapter-plan.md``.
"""

from .adapter import SpringServeAdapter
from .client import (
    SpringServeAPIError,
    SpringServeAuthError,
    SpringServeClient,
    SpringServeError,
    SpringServeForbiddenError,
    SpringServeNotFoundError,
    SpringServeRateLimitError,
    SpringServeServerError,
    SpringServeValidationError,
)
from .schemas import SpringServeConnectionConfig, SpringServeProductConfig

__all__ = [
    "SpringServeAPIError",
    "SpringServeAdapter",
    "SpringServeAuthError",
    "SpringServeClient",
    "SpringServeConnectionConfig",
    "SpringServeError",
    "SpringServeForbiddenError",
    "SpringServeNotFoundError",
    "SpringServeProductConfig",
    "SpringServeRateLimitError",
    "SpringServeServerError",
    "SpringServeValidationError",
]
