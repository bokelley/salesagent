"""Sandbox/no-spend helpers for buyer-facing protocol paths."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from adcp.types.generated_poc.core.account_ref import AccountReference2

from src.core.database.models import Account
from src.core.resolved_identity import ResolvedIdentity

logger = logging.getLogger(__name__)

INTERCHANGE_SANDBOX_ZERO_RATE_CARD = "interchange-sandbox-zero-price"
SANDBOX_TRAFFICKING_FLAG = "_sandbox_trafficking"
SANDBOX_TRAFFICKING_LINE_ITEM_TYPE: Literal["HOUSE"] = "HOUSE"
SANDBOX_TRAFFICKING_PRIORITY: Literal[16] = 16
SANDBOX_TRAFFICKING_PRICING_MODEL: Literal["cpm"] = "cpm"
SANDBOX_TRAFFICKING_COST_TYPE: Literal["CPM"] = "CPM"


@dataclass(frozen=True)
class SandboxMode:
    """Resolved sandbox/no-spend state for a buyer request."""

    active: bool
    sources: tuple[str, ...] = ()
    account_id: str | None = None
    rate_card: str | None = None

    @property
    def diagnostic(self) -> dict[str, Any]:
        return {
            "sandbox": self.active,
            "sources": list(self.sources),
            "account_id": self.account_id,
            "rate_card": self.rate_card,
        }


def _is_sandbox_account(account: Account | None) -> bool:
    if account is None:
        return False
    return bool(account.sandbox) or account.rate_card == INTERCHANGE_SANDBOX_ZERO_RATE_CARD


def account_ref_from_request(req: Any) -> Any | None:
    """Return an explicit request account reference without triggering mock attrs."""
    model_fields = getattr(req.__class__, "model_fields", None)
    if isinstance(model_fields, dict) and "account" in model_fields:
        return getattr(req, "account", None)

    model_extra = getattr(req, "model_extra", None)
    if isinstance(model_extra, dict) and "account" in model_extra:
        return model_extra["account"]

    request_attrs = getattr(req, "__dict__", None)
    if isinstance(request_attrs, dict) and "account" in request_attrs:
        return request_attrs["account"]

    return None


def _source_for_account(account: Account) -> str:
    if account.sandbox:
        return "account.sandbox"
    return "account.rate_card"


def sandbox_mode_for_request(
    *,
    identity: ResolvedIdentity,
    account_ref: Any | None = None,
) -> SandboxMode:
    """Resolve sandbox/no-spend mode from account routing context.

    This function is read-only and depends on the transport boundary's resolved
    ``identity.account_id`` for persisted account metadata. Natural-key
    references only activate sandbox mode when they explicitly carry
    ``sandbox=True``.
    """
    sources: list[str] = []
    account_id: str | None = None
    rate_card: str | None = None

    tenant_id = identity.tenant_id
    account: Account | None = None
    account_snapshot: tuple[str, str | None, bool | None] | None = None
    if tenant_id is not None and identity.account_id is not None:
        from src.core.database.repositories.uow import AccountUoW

        with AccountUoW(tenant_id) as uow:
            assert uow.accounts is not None
            account = uow.accounts.get_by_id(identity.account_id)
            if account is not None:
                account_snapshot = (account.account_id, account.rate_card, account.sandbox)

    ref = getattr(account_ref, "root", account_ref)
    if account is None and isinstance(ref, AccountReference2) and ref.sandbox:
        sources.append("account_ref.sandbox")

    if account_snapshot is not None:
        account_id, rate_card, account_sandbox = account_snapshot
        if account_sandbox is True or rate_card == INTERCHANGE_SANDBOX_ZERO_RATE_CARD:
            sources.append("account.sandbox" if account_sandbox else "account.rate_card")

    return SandboxMode(active=bool(sources), sources=tuple(sources), account_id=account_id, rate_card=rate_card)


def sandbox_mode_for_rows(*, account: Account | None = None) -> SandboxMode:
    """Resolve sandbox/no-spend mode from already-loaded ORM/schema rows."""
    sources: list[str] = []
    if account is not None and _is_sandbox_account(account):
        sources.append(_source_for_account(account))
    return SandboxMode(
        active=bool(sources),
        sources=tuple(sources),
        account_id=account.account_id if account is not None else None,
        rate_card=account.rate_card if account is not None else None,
    )


def _zero_guidance(guidance: Any) -> Any:
    if guidance is None:
        return None

    if isinstance(guidance, dict):
        return {
            key: (0 if key in {"floor", "p25", "p50", "p75", "p90"} and value is not None else value)
            for key, value in guidance.items()
        }

    updates = {
        field: 0 for field in ("floor", "p25", "p50", "p75", "p90") if getattr(guidance, field, None) is not None
    }
    if not updates:
        return guidance
    if hasattr(guidance, "model_copy"):
        return guidance.model_copy(update=updates)
    for field, value in updates.items():
        setattr(guidance, field, value)
    return guidance


def _zero_pricing_option(option: Any) -> Any:
    inner = getattr(option, "root", option)
    updates: dict[str, Any] = {}
    for field in ("fixed_price", "floor_price", "rate", "min_spend_per_package"):
        if getattr(inner, field, None) is not None:
            updates[field] = 0

    guidance = getattr(inner, "price_guidance", None)
    zero_guidance = _zero_guidance(guidance)
    if zero_guidance is not guidance:
        updates["price_guidance"] = zero_guidance

    if not updates:
        return option

    zero_inner = inner.model_copy(update=updates) if hasattr(inner, "model_copy") else inner
    if not hasattr(inner, "model_copy"):
        for field, value in updates.items():
            setattr(zero_inner, field, value)

    if hasattr(option, "root"):
        if hasattr(option, "model_copy"):
            return option.model_copy(update={"root": zero_inner})
        return type(option)(root=zero_inner)
    return zero_inner


def zero_pricing_for_sandbox(products: list[Any]) -> list[Any]:
    """Zero buyer-visible prices/floors/rates on product pricing options."""
    for product in products:
        wire_product = getattr(product, "wire", product)
        pricing_options = getattr(wire_product, "pricing_options", None)
        if pricing_options:
            wire_product.pricing_options = [_zero_pricing_option(option) for option in pricing_options]
    return products


def mark_sandbox_trafficking_request(req: Any) -> None:
    """Mark a request so adapters can choose sandbox trafficking behavior."""
    object.__setattr__(req, SANDBOX_TRAFFICKING_FLAG, True)


def is_sandbox_trafficking_request(req: Any) -> bool:
    """Return whether a request should create no-spend sandbox traffic."""
    return bool(getattr(req, SANDBOX_TRAFFICKING_FLAG, False))


def sandbox_trafficking_pricing_info(
    package_pricing_info: dict[str, dict[str, Any]],
    *,
    default_currency: str = "USD",
) -> dict[str, dict[str, Any]]:
    """Normalize platform-facing package economics for sandbox trafficking.

    Buyer-supplied rates/bids are still accepted for validation context, but
    ad-server creation must not use them. The platform receives a fixed CPM
    price of zero and a marker that adapter code can use for line-item type.
    """
    normalized: dict[str, dict[str, Any]] = {}
    for package_id, pricing in package_pricing_info.items():
        currency = str(pricing.get("currency") or default_currency)
        normalized[package_id] = {
            **pricing,
            "pricing_model": SANDBOX_TRAFFICKING_PRICING_MODEL,
            "rate": 0.0,
            "currency": currency,
            "is_fixed": True,
            "bid_price": None,
            "sandbox_trafficking": True,
        }
    return normalized


def sandbox_trafficking_packages(packages: list[Any]) -> list[Any]:
    """Return packages with no platform budget and a viable line-item goal."""
    adjusted: list[Any] = []
    for package in packages:
        impressions = max(int(getattr(package, "impressions", 0) or 0), 1)
        updates = {"budget": 0.0, "impressions": impressions}
        if hasattr(package, "model_copy"):
            adjusted.append(package.model_copy(update=updates))
            continue
        for field, value in updates.items():
            setattr(package, field, value)
        adjusted.append(package)
    return adjusted
