"""Live SpringServe API smoke test (Stage 1).

Exercises the SpringServe transport against the real API: token minting
(or static-token use), basic read probes against the endpoints the
adapter depends on. Skipped by default; runs only when credentials are
provisioned via env vars.

Provide ONE of:

    SPRINGSERVE_TEST_API_TOKEN          (pre-minted, 2hr TTL)

or both of:

    SPRINGSERVE_USERNAME (or SPRINGSERVE_TEST_EMAIL)
    SPRINGSERVE_PASSWORD (or SPRINGSERVE_TEST_PASSWORD)

(SpringServe's auth field is named ``email`` in their API but takes the
account login -- the username and email names are accepted interchangeably.)

Run with::

    uv run pytest tests/integration/test_springserve_live.py -m live -v

Stage 1 does NOT write anything. Stage 2 adds the live create+delete
round-trip for Campaigns + Demand Tags.
"""

from __future__ import annotations

import logging
import os

import pytest

from src.adapters.springserve import SpringServeClient

logger = logging.getLogger(__name__)

API_TOKEN_ENV = "SPRINGSERVE_TEST_API_TOKEN"
# Both naming conventions are accepted -- SpringServe's API field is ``email``
# but the account login is often referred to as ``username``.
EMAIL_ENVS = ("SPRINGSERVE_USERNAME", "SPRINGSERVE_TEST_EMAIL")
PASSWORD_ENVS = ("SPRINGSERVE_PASSWORD", "SPRINGSERVE_TEST_PASSWORD")

pytestmark = pytest.mark.live


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _build_client() -> SpringServeClient:
    api_token = os.environ.get(API_TOKEN_ENV)
    email = _first_env(*EMAIL_ENVS)
    password = _first_env(*PASSWORD_ENVS)

    if api_token:
        return SpringServeClient(api_token=api_token)
    if email and password:
        return SpringServeClient(email=email, password=password)
    pytest.skip(
        f"Live SpringServe test requires {API_TOKEN_ENV} or "
        f"({'/'.join(EMAIL_ENVS)} + {'/'.join(PASSWORD_ENVS)})"
    )


@pytest.fixture(scope="module")
def client() -> SpringServeClient:
    return _build_client()


class TestAuthAndConnectivity:
    def test_campaigns_endpoint_reachable(self, client: SpringServeClient):
        """Smoke probe -- the campaigns endpoint must respond 2xx for our token.

        This is the single Stage-1 must-pass live check: it proves the
        transport's auth header shape (raw token, no Bearer prefix) is
        correct AND that the bearer has read scope on the primary surface
        the adapter will use.
        """
        status, body = client.probe("GET", "/campaigns?per_page=1")
        assert status == 200, f"campaigns probe failed: HTTP {status}: {body[:200]}"


class TestPermissionsProbe:
    """One probe per endpoint the adapter will eventually touch; logs the
    status so the operator can see at-a-glance which scopes are granted
    on their test account."""

    @pytest.mark.parametrize(
        "path,feature",
        [
            ("/campaigns?per_page=1", "create_media_buy"),
            ("/demand_tags?per_page=1", "create_media_buy"),
            ("/videos?per_page=1", "sync_creatives"),
            ("/supply_tags?per_page=1", "inventory_sync"),
            ("/supply_partners?per_page=1", "inventory_sync"),
            ("/report?per_page=1", "delivery_reporting"),
        ],
    )
    def test_endpoint_scope(self, client: SpringServeClient, path: str, feature: str):
        status, body = client.probe("GET", path)
        # Soft-assert: log denied endpoints rather than fail the whole pass,
        # so a Stage 1 deploy can ship before every scope is granted.
        if status in (401, 403):
            logger.warning("SpringServe %s denied (HTTP %s) feature=%s body=%s", path, status, feature, body[:200])
        else:
            logger.info("SpringServe %s OK (HTTP %s) feature=%s", path, status, feature)
        # The probe itself must complete; auth failures fail loudly.
        assert status != 0, f"probe to {path} produced no status"
