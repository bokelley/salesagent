"""Sandbox zero-rate-card enforcement.

Embedded sandbox accounts are allowed to exercise discovery and media-buy
validation, but they must never expose billable pricing or create real spend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from adcp.types.generated_poc.core.ext import ExtensionObject

from src.core.database.repositories.uow import AccountUoW
from src.core.resolved_identity import ResolvedIdentity
from src.core.resolved_product import ResolvedProduct
from src.core.testing_hooks import AdCPTestContext

logger = logging.getLogger(__name__)

INTERCHANGE_SANDBOX_ZERO_RATE_CARD = "interchange-sandbox-zero-price"

_ZERO_PRICE_FIELDS: tuple[str, ...] = (
    "fixed_price",
    "floor_price",
    "rate",
    "price",
    "min_spend_per_package",
)
_PRICE_GUIDANCE_FIELDS: tuple[str, ...] = ("floor", "p25", "p50", "p75", "p90")


@dataclass(frozen=True)
class SandboxZeroRatePolicy:
    """Resolved account policy requiring no-spend sandbox behavior."""

    account_id: str
    sandbox: bool
    rate_card: str | None
    reason: str

    def diagnostics(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "sandbox": self.sandbox,
            "rate_card": self.rate_card,
            "reason": self.reason,
            "pricing": "zero",
            "spend": "dry_run",
        }

    def extension(self) -> ExtensionObject:
        return ExtensionObject.model_validate({"salesagent_sandbox": self.diagnostics()})


def get_sandbox_zero_rate_policy(identity: ResolvedIdentity | None) -> SandboxZeroRatePolicy | None:
    """Return sandbox zero-rate policy for the resolved account, if any."""
    if identity is None or identity.tenant_id is None or identity.account_id is None:
        return None

    with AccountUoW(identity.tenant_id) as uow:
        assert uow.accounts is not None
        account = uow.accounts.get_by_id(identity.account_id)
        if account is None:
            return None

        account_id = account.account_id
        sandbox = bool(account.sandbox)
        rate_card = account.rate_card
    zero_rate_card = rate_card == INTERCHANGE_SANDBOX_ZERO_RATE_CARD
    if not sandbox and not zero_rate_card:
        return None

    reasons = []
    if sandbox:
        reasons.append("sandbox_account")
    if zero_rate_card:
        reasons.append("zero_rate_card")

    return SandboxZeroRatePolicy(
        account_id=account_id,
        sandbox=sandbox,
        rate_card=rate_card,
        reason="+".join(reasons),
    )


def force_no_spend_testing_context(testing_ctx: AdCPTestContext, policy: SandboxZeroRatePolicy) -> AdCPTestContext:
    """Force a testing context into dry-run/no-spend mode for sandbox accounts."""
    if testing_ctx.dry_run and testing_ctx.simulated_spend and testing_ctx.simulated_spend_amount == 0.0:
        return testing_ctx

    logger.info(
        "[SANDBOX_ZERO_RATE] Forcing dry-run media-buy execution for account_id=%s rate_card=%s reason=%s",
        policy.account_id,
        policy.rate_card,
        policy.reason,
    )
    return testing_ctx.model_copy(
        update={
            "dry_run": True,
            "simulated_spend": True,
            "simulated_spend_amount": 0.0,
        }
    )


def apply_zero_rate_card_to_products(
    products: list[ResolvedProduct],
    policy: SandboxZeroRatePolicy,
) -> None:
    """Mutate buyer-visible product pricing to zero for sandbox discovery."""
    for product in products:
        for option in product.pricing_options or []:
            _zero_pricing_option(option)
        _annotate_product(product, policy)


def _zero_pricing_option(option: Any) -> None:
    inner = getattr(option, "root", option)
    for field_name in _ZERO_PRICE_FIELDS:
        _set_zero_if_present(inner, field_name)

    price_guidance = _get_value(inner, "price_guidance")
    if price_guidance is not None:
        for field_name in _PRICE_GUIDANCE_FIELDS:
            _set_zero_if_present(price_guidance, field_name)


def _annotate_product(product: ResolvedProduct, policy: SandboxZeroRatePolicy) -> None:
    wire = product.wire
    current_ext: Any = getattr(wire, "ext", None)
    if hasattr(current_ext, "model_dump"):
        ext = current_ext.model_dump(mode="json", exclude_none=True)
    else:
        ext = dict(current_ext or {})
    ext["salesagent_sandbox"] = policy.diagnostics()
    wire.ext = ExtensionObject.model_validate(ext)


def _get_value(obj: Any, field_name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(field_name)
    return getattr(obj, field_name, None)


def _set_zero_if_present(obj: Any, field_name: str) -> None:
    if isinstance(obj, dict):
        if field_name in obj:
            obj[field_name] = 0.0
        return
    if hasattr(obj, field_name):
        setattr(obj, field_name, 0.0)
