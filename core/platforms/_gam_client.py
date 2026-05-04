"""GAM client construction shared between platforms and probes.

Reads the per-tenant ``adapter_config`` row, decrypts the
``gam_service_account_json``, builds a googleads ``AdManagerClient``
authenticated as the SA. Cached per-tenant so the platform doesn't
rebuild on every request.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from googleads import ad_manager, oauth2
from sqlalchemy import select

from src.core.database.database_session import get_db_session
from src.core.database.models import AdapterConfig

GAM_SCOPES = ["https://www.googleapis.com/auth/dfp"]


class _ServiceAccountOAuthClient(oauth2.GoogleOAuth2Client):
    """googleads OAuth2 wrapper around a google-auth ``Credentials``."""

    def __init__(self, credentials: service_account.Credentials) -> None:
        self._creds = credentials

    def CreateHttpHeader(self) -> dict[str, str]:
        if not self._creds.valid:
            self._creds.refresh(GoogleAuthRequest())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def Refresh(self) -> None:
        self._creds.refresh(GoogleAuthRequest())


@lru_cache(maxsize=32)
def get_gam_client(tenant_id: str) -> ad_manager.AdManagerClient:
    """Return a cached GAM client for ``tenant_id``.

    Reads adapter_config (which decrypts the service account JSON via
    its property getter), builds google-auth Credentials, wraps in the
    googleads OAuth2 client adapter, and constructs the AdManagerClient.

    Cached per-tenant; the underlying ``Credentials`` self-refreshes
    when its token expires, so the cache entry stays valid for the
    process lifetime.
    """
    with get_db_session() as session:
        cfg = session.scalars(
            select(AdapterConfig).filter_by(tenant_id=tenant_id)
        ).first()

    if cfg is None:
        raise ValueError(f"No adapter_config row for tenant {tenant_id!r}")
    if cfg.adapter_type != "google_ad_manager":
        raise ValueError(
            f"Tenant {tenant_id!r} adapter_type is {cfg.adapter_type!r}, not google_ad_manager"
        )
    if not cfg.gam_service_account_json:
        raise ValueError(f"Tenant {tenant_id!r} has no service account JSON configured")
    if not cfg.gam_network_code:
        raise ValueError(f"Tenant {tenant_id!r} has no GAM network code configured")

    sa_info: dict[str, Any] = json.loads(cfg.gam_service_account_json)
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=GAM_SCOPES
    )
    return ad_manager.AdManagerClient(
        oauth2_client=_ServiceAccountOAuthClient(credentials),
        application_name="salesagent-greenfield-rebuild",
        network_code=cfg.gam_network_code,
        cache=None,
    )
