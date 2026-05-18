"""Translate AdCP targeting into SpringServe demand-tag fields.

SpringServe demand tags don't wrap targeting in a nested ``targeting``
object -- the targeting fields (``country_codes``, ``state_codes``,
``metro_area_codes``, ``player_sizes``, ``user_agent_devices``, and the
supply-side ``demand_tag_priorities``) live directly on the tag, each
paired with a ``<dimension>_targeting`` discriminator (``"All"`` vs
``"White List"``).

Signal resolution: buyer-supplied ``audience_include`` / ``audience_exclude``
references resolve through operator-declared ``TenantSignal`` rows whose
``adapter_config`` carries SpringServe-specific kinds. Today the only
shipping kind is ``springserve_value_list`` (publisher-curated audience
lists from the KV catalog); each signal produces one ``demand_tag_keys``
entry with ``list_type='white_list'`` or ``'black_list'`` per the
include/exclude mode.

Wire format for KV targeting (verified live on Talpa demand tags
2025-08-08 — see ``scripts/springserve_compare_wire.py`` for the probe)::

    {
      "key_value_targeting": true,
      "demand_tag_keys": [
        {
          "key_id": 3997,                          # SpringServe key id
          "list_type": "white_list"|"black_list",  # include/exclude
          "demand_tag_key_type": "values",
          "key_required": true,
          "group": "1",                            # AND across distinct groups
          "free_values": [],
          "value_ids": [],
          "value_list_ids": [2937]                 # SpringServe value_list id
        },
        ...
      ]
    }

Each value_list_id selects one publisher-curated audience (e.g. "Podcast
MV35-54" -> the station IDs covering that demographic). Multiple include
signals on the same key OR within one group; signals on distinct keys AND
across groups -- the same semantics SpringServe's own UI uses.

This module produces the kwarg dict consumed by
:class:`SpringServeDemandTagsClient.create` so adapter code doesn't have
to know the wire-format conventions.

**Known API limitation (May 2026):** The SpringServe v0 API silently
drops ``key_value_targeting`` + ``demand_tag_keys`` on both POST
``/demand_tags`` and PUT ``/demand_tags/<id>``. The wire format above
matches what live demand_tags return on READ -- but the WRITE path
returns HTTP 200 with both fields unset. Variant shapes attempted
(``targeting_keys``, ``targeting_keys_attributes``, ``demand_tag_keys_attributes``,
``free_values`` instead of ``value_list_ids``) all behave the same. Until
SpringServe enables write access (likely via a higher API scope than the
AdOps role we currently have, or a separate ``/demand_tag_keys``
endpoint), the materializer emits structurally correct payloads, the
adapter logs a warning, and the resulting demand tag will not actually
filter on the requested audience. Tracking issue: ask Mathijs.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_demand_tag_targeting(
    targeting_overlay: Any,
    product_config: dict[str, Any] | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Build the targeting kwargs for ``SpringServeDemandTagsClient.create``.

    Inputs:
        targeting_overlay: AdCP ``Targeting`` model (geo, device,
            ``audience_include``, ``audience_exclude``).
        product_config: ``SpringServeProductConfig`` as a dict -- supplies
            product-default supply tag inclusion, player sizes, and
            environments.
        tenant_id: When provided, ``audience_include``/``audience_exclude``
            references resolve through the tenant's ``tenant_signals``.
            Required for signal resolution; if omitted, signals are
            ignored (preserves the existing rejection in ``validate_targeting``).

    Output keys correspond 1:1 to Demand Tag fields. Empty values are
    omitted so the client builder doesn't override SpringServe defaults.
    """
    product_config = product_config or {}
    kwargs: dict[str, Any] = {}

    # Supply targeting -- product config carries supply_tag_ids, we turn them
    # into demand_tag_priorities entries (priority + tier default to 1).
    supply_tag_ids = product_config.get("supply_tag_ids") or []
    if supply_tag_ids:
        kwargs["demand_tag_priorities"] = [
            {"supply_tag_id": int(stid), "priority": 1, "tier": 1} for stid in supply_tag_ids
        ]

    # Player + device defaults from product config.
    if product_config.get("player_sizes"):
        kwargs["player_sizes"] = list(product_config["player_sizes"])
    if product_config.get("device_types"):
        kwargs["user_agent_devices"] = list(product_config["device_types"])

    # AdCP-overlay-driven geo. Empty lists in the overlay are no-ops; we
    # only set targeting when there's actually a list.
    if targeting_overlay is not None:
        if getattr(targeting_overlay, "geo_countries", None):
            kwargs["country_codes"] = [c.root for c in targeting_overlay.geo_countries]
        if getattr(targeting_overlay, "geo_regions", None):
            kwargs["state_codes"] = [r.root for r in targeting_overlay.geo_regions]
        if getattr(targeting_overlay, "geo_metros", None):
            metro_values: list[str] = []
            for metro in targeting_overlay.geo_metros:
                metro_values.extend(metro.values)
            if metro_values:
                kwargs["metro_area_codes"] = metro_values
        if getattr(targeting_overlay, "device_type_any_of", None):
            # AdCP device-type overlay wins over product defaults when both
            # are set -- buyer intent is more specific than product defaults.
            kwargs["user_agent_devices"] = list(targeting_overlay.device_type_any_of)

    # Resolve operator-declared signals referenced in audience_include /
    # audience_exclude. Each value_list signal produces one demand_tag_keys
    # entry; multiple signals targeting the same SpringServe key share a
    # group (OR within key); signals on distinct keys land in distinct
    # groups (AND across keys).
    if tenant_id and targeting_overlay is not None:
        include_ids = list(getattr(targeting_overlay, "audience_include", None) or [])
        exclude_ids = list(getattr(targeting_overlay, "audience_exclude", None) or [])
        if include_ids or exclude_ids:
            demand_tag_keys = _resolve_audience_signals(
                tenant_id=tenant_id,
                include_signal_ids=include_ids,
                exclude_signal_ids=exclude_ids,
            )
            if demand_tag_keys:
                kwargs["demand_tag_keys"] = demand_tag_keys
                kwargs["key_value_targeting"] = True
                # See module docstring -- SpringServe's v0 API currently
                # drops these fields on write. Surface a one-line warning
                # at translation time so the audit trail makes the gap
                # visible without per-signal noise.
                logger.warning(
                    "SpringServe demand_tag_keys produced (%d entries) but the v0 API may silently "
                    "ignore them on write; see src/adapters/springserve/targeting.py module docstring.",
                    len(demand_tag_keys),
                )

    # Escape hatch -- raw demand-tag field overrides (extras win).
    extras = product_config.get("extra_demand_tag_fields") or {}
    if isinstance(extras, dict):
        for key, value in extras.items():
            kwargs[key] = value

    return kwargs


def validate_targeting(targeting_overlay: Any) -> list[str]:
    """Return a list of unsupported-targeting messages for SpringServe.

    Buyers see a clear ``unsupported_targeting`` error rather than have a
    dimension silently dropped at translation time. The Stage-2 cut rejects
    overlays whose wire format isn't verified against the live API yet --
    subsequent stages narrow this list as fields move from "unverified" to
    "verified" against the live account.
    """
    unsupported: list[str] = []
    if targeting_overlay is None:
        return unsupported

    if getattr(targeting_overlay, "geo_postal_areas", None) or getattr(
        targeting_overlay, "geo_postal_areas_exclude", None
    ):
        unsupported.append("Postal-area targeting not supported -- use geo_metros (DMA) or geo_regions instead")

    if getattr(targeting_overlay, "frequency_cap", None):
        unsupported.append(
            "Frequency cap targeting pending SpringServe sandbox validation -- "
            "set frequency caps via SpringServeProductConfig escape hatch for now"
        )

    if getattr(targeting_overlay, "audiences_any_of", None):
        unsupported.append(
            "audiences_any_of is the legacy free-form audience field; use audience_include "
            "with operator-declared SpringServe signal_ids instead."
        )

    if getattr(targeting_overlay, "dayparting", None):
        unsupported.append("Free-form dayparting pending SpringServe sandbox validation")

    return unsupported


# ---------------------------------------------------------------------------
# Signal resolution (SpringServe)
# ---------------------------------------------------------------------------


def _resolve_audience_signals(
    *,
    tenant_id: str,
    include_signal_ids: list[str],
    exclude_signal_ids: list[str],
) -> list[dict[str, Any]]:
    """Look up each signal_id in ``tenant_signals`` and emit the
    ``demand_tag_keys`` list SpringServe writes on a demand tag.

    Includes become ``list_type='white_list'``; excludes become
    ``'black_list'``. Signals targeting the same SpringServe key share a
    group so multiple value_lists OR within one key; distinct keys get
    distinct groups so they AND with each other -- this matches the
    semantics of SpringServe's own UI.

    Composed signals are not yet supported -- only passthrough signals
    with ``kind='springserve_value_list'`` resolve. Anything else raises
    a clear error so operators know to either re-author or pick a
    pass-through signal.
    """
    from src.core.database.repositories.uow import TenantSignalUoW

    all_ids = list(include_signal_ids) + list(exclude_signal_ids)
    if not all_ids:
        return []

    with TenantSignalUoW(tenant_id) as uow:
        assert uow.tenant_signals is not None
        signals_by_id = {s.signal_id: s for s in uow.tenant_signals.list_by_ids(all_ids)}
        missing = [sid for sid in all_ids if sid not in signals_by_id]
        if missing:
            raise ValueError(
                f"SpringServe audience targeting references signal(s) not declared on tenant "
                f"{tenant_id!r}: {', '.join(sorted(missing))}. "
                f"Map each signal from the Signals page first."
            )

        # Collect (key_id, value_list_id, list_type) tuples, then bucket by
        # (key_id, list_type) so multiple includes on the same key produce
        # a single demand_tag_keys entry with multiple value_list_ids.
        # NB: SpringServe doesn't allow include+exclude of the same key in
        # one tag (the semantics would be contradictory); we keep them as
        # separate entries since the API does -- it'll reject illogical
        # combinations server-side and surface a clean validation error.
        atoms: list[tuple[int, int, str, str]] = []  # (key_id, value_list_id, list_type, key_name)
        for sid in include_signal_ids:
            for atom in _signal_atoms(signals_by_id[sid]):
                atoms.append((*atom, "white_list"))
        for sid in exclude_signal_ids:
            for atom in _signal_atoms(signals_by_id[sid]):
                atoms.append((*atom, "black_list"))

    # Group atoms by (key_id, list_type) into one demand_tag_keys entry per
    # group. Each distinct key gets a different ``group`` index so the
    # SpringServe AND-across-keys / OR-within-key semantics hold.
    buckets: dict[tuple[int, str], dict[str, Any]] = {}
    group_index_by_key: dict[int, int] = {}
    next_group = 1
    for key_id, value_list_id, _key_name, list_type in atoms:
        if key_id not in group_index_by_key:
            group_index_by_key[key_id] = next_group
            next_group += 1
        bucket_key = (key_id, list_type)
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "key_id": key_id,
                "list_type": list_type,
                "demand_tag_key_type": "values",
                "key_required": True,
                "group": str(group_index_by_key[key_id]),
                "free_values": [],
                "value_ids": [],
                "value_list_ids": [],
            }
        if value_list_id not in buckets[bucket_key]["value_list_ids"]:
            buckets[bucket_key]["value_list_ids"].append(value_list_id)
    # Stable order so the wire output is reproducible (tests + diffs).
    return sorted(buckets.values(), key=lambda b: (b["key_id"], b["list_type"]))


def _signal_atoms(signal) -> list[tuple[int, int, str]]:
    """Return ``[(key_id, value_list_id, key_name), ...]`` for one signal.

    Today only ``kind='springserve_value_list'`` resolves; each row is
    one (key, value_list) pair. Composed signals (multiple value_lists
    glued by criteria) aren't yet supported on the SpringServe side.
    """
    cfg = signal.adapter_config or {}
    config_type = cfg.get("type")
    kind = cfg.get("kind")

    if config_type == "composed":
        raise ValueError(
            f"Signal {signal.signal_id!r} type='composed' is not yet supported by SpringServe -- "
            f"author each value_list as its own passthrough signal and reference them all in "
            f"audience_include instead."
        )
    if kind != "springserve_value_list":
        raise ValueError(
            f"Signal {signal.signal_id!r} adapter_config.kind={kind!r} is not supported by "
            f"SpringServe. Expected kind='springserve_value_list' (a publisher-curated value_list "
            f"from the KV catalog)."
        )

    key_id = cfg.get("key_id")
    value_list_id = cfg.get("value_list_id")
    key_name = cfg.get("key_name") or ""
    if not key_id or not value_list_id:
        raise ValueError(
            f"Signal {signal.signal_id!r} kind='springserve_value_list' missing key_id or "
            f"value_list_id in adapter_config."
        )
    return [(int(key_id), int(value_list_id), str(key_name))]


# Backwards-compatible alias for callers still importing the old name.
build_targeting = build_demand_tag_targeting
