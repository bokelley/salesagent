"""Admin blueprint for managing tenant signals.

Operator authoring surface for ``TenantSignal`` — the publisher's
first-party map of "what targeting can a buyer apply on this inventory."
Storefronts discover signals through the AdCP ``get_signals`` tool;
buyers reference the resulting ``signal_id`` in
``audience_include`` / ``audience_exclude`` on ``create_media_buy``.

UX (post-#465):

- **Landing** (``/signals/``) is the operator's primary surface. Three
  states depending on synced inventory:
  1. **No inventory** — empty state with a "Sync inventory first" CTA.
  2. **Has unmapped inventory** — bulk-map table on top (segments tab +
     KV-pairs tab) showing every synced GAM entity, with a "Mapped as X"
     badge inline for rows that already have a signal. Operator ticks
     boxes → ``POST /signals/bulk-create`` mints one TenantSignal per
     row in a single transaction.
  3. **Everything mapped** — just the existing signals library below
     the bulk panel.
- **Composite builder** (``/signals/composite``) is the rare-path
  surface: multi-key AND, OR-groups, exclude. Embeds the same
  ``TargetingWidget`` product authoring uses.
- **Detail / edit** (``/signals/<signal_id>/edit``) — rename, edit
  description, delete. Where ``signal_id`` is visible (monospace,
  copyable) for ops debugging.

The legacy ``/signals/add`` route is gone — see #458/#462/#464 in git
history if you need the source-picker UI back.
"""

from __future__ import annotations

import json
import logging
import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.signal_id import unique_signal_id
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantSignal
from src.core.database.repositories.gam_sync import GAMSyncRepository
from src.core.database.repositories.tenant_signal import TenantSignalRepository

logger = logging.getLogger(__name__)

tenant_signals_bp = Blueprint("tenant_signals", __name__)

_VALID_VALUE_TYPES = ("binary", "categorical", "numeric")
_SIGNAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _parse_float(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


# ---------------------------------------------------------------------------
# Landing page — bulk-map + existing library
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/")
@require_tenant_access()
def list_signals(tenant_id: str):
    """Signals library landing.

    Three rendered states (see module docstring). All three are the same
    template — the conditional branches inside it decide what to show.
    """
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))

        signal_repo = TenantSignalRepository(session, tenant_id)
        segment_index, kv_index = signal_repo.mapped_index()

        # Load synced GAM inventory rows (the bulk-map source). Empty when
        # the tenant hasn't synced — that's State A (the "sync first" CTA).
        gam_repo = GAMSyncRepository(session, tenant_id)
        segments_rows = gam_repo.list_inventory("audience_segment")
        keys_rows = gam_repo.list_inventory("custom_targeting_key")

        segments = [
            {
                "id": row.inventory_id,
                "name": row.name,
                "size": (row.inventory_metadata or {}).get("size"),
                "type": (row.inventory_metadata or {}).get("type"),
                "mapped_signal_id": (
                    segment_index[row.inventory_id].signal_id if row.inventory_id in segment_index else None
                ),
                "mapped_signal_name": (
                    segment_index[row.inventory_id].name if row.inventory_id in segment_index else None
                ),
            }
            for row in segments_rows
        ]
        keys = [
            {
                "id": row.inventory_id,
                "name": row.name,
                "display_name": (row.inventory_metadata or {}).get("display_name") or row.name,
                "type": (row.inventory_metadata or {}).get("type", "UNKNOWN"),
            }
            for row in keys_rows
        ]

        rows = signal_repo.list_all()
        signals = [
            {
                "signal_id": row.signal_id,
                "name": row.name,
                "description": row.description,
                "value_type": row.value_type,
                "categories": row.categories or [],
                "adapter_kind": (row.adapter_config or {}).get("kind"),
                "is_composed": (row.adapter_config or {}).get("type") == "composed",
                "is_complex": (row.adapter_config or {}).get("kind") == "gam_targeting_groups",
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    has_inventory = bool(segments or keys)
    # Bulk-map shows UN-mapped rows only — the mapped ones already appear
    # in the Existing-signals library below. Surface a count so operators
    # know how many have already been mapped (and can flip back via the
    # signals library's edit page).
    unmapped_segments = [s for s in segments if not s["mapped_signal_id"]]
    mapped_segments_count = len(segments) - len(unmapped_segments)
    return render_template(
        "tenant_signals_list.html",
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        signals=signals,
        segments=unmapped_segments,
        mapped_segments_count=mapped_segments_count,
        keys=keys,
        kv_index_size=len(kv_index),
        has_inventory=has_inventory,
    )


# ---------------------------------------------------------------------------
# Bulk-map: turn ticked GAM entities into TenantSignal rows in one txn
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/bulk-create", methods=["POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("bulk_create_tenant_signals")
def bulk_create(tenant_id: str):
    """Mint one TenantSignal per ticked row from the bulk-map UI.

    Request: JSON ``{"items": [{"kind": "audience_segment"|
    "custom_key_value", ...source-specific ids...}, ...]}``.
    For segments: ``{"kind": "audience_segment", "segment_id":
    "...", "segment_name": "..."}``. For KVs: ``{"kind":
    "custom_key_value", "key_id": "...", "value_id": "...",
    "key_name": "...", "value_name": "..."}``.

    Names auto-derive from the supplied display names; ``signal_id``
    slugified + collision-disambiguated. Rows that would duplicate an
    existing mapping silently skip — the UI already prevented re-checking
    them, this is the defensive backstop.

    Returns JSON ``{"created": N, "skipped": [signal_id, ...]}``.
    """
    payload = request.get_json(silent=True) or {}
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items must be a non-empty list"}), 400

    created: list[str] = []
    skipped: list[str] = []
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            return jsonify({"error": "tenant not found"}), 404
        repo = TenantSignalRepository(session, tenant_id)
        segment_index, kv_index = repo.mapped_index()

        for item in items:
            kind = item.get("kind")
            if kind == "audience_segment":
                segment_id = str(item.get("segment_id") or "")
                segment_name = str(item.get("segment_name") or f"Segment {segment_id}")
                if not segment_id:
                    continue
                if segment_id in segment_index:
                    skipped.append(segment_index[segment_id].signal_id)
                    continue
                signal_id = unique_signal_id(segment_name, exists=lambda sid: repo.get_by_id(sid) is not None)
                signal = TenantSignal(
                    tenant_id=tenant_id,
                    signal_id=signal_id,
                    name=segment_name,
                    value_type="binary",
                    adapter_config={
                        "type": "passthrough",
                        "kind": "audience_segment",
                        "segment_id": segment_id,
                    },
                    data_provider="publisher",
                    targeting_dimension="audience",
                )
                repo.add(signal)
                created.append(signal_id)
            elif kind == "custom_key_value":
                key_id = str(item.get("key_id") or "")
                value_id = str(item.get("value_id") or "")
                key_name = str(item.get("key_name") or f"Key {key_id}")
                value_name = str(item.get("value_name") or f"Value {value_id}")
                if not key_id or not value_id:
                    continue
                if (key_id, value_id) in kv_index:
                    skipped.append(kv_index[(key_id, value_id)].signal_id)
                    continue
                derived_name = f"{key_name}={value_name}"
                signal_id = unique_signal_id(derived_name, exists=lambda sid: repo.get_by_id(sid) is not None)
                signal = TenantSignal(
                    tenant_id=tenant_id,
                    signal_id=signal_id,
                    name=derived_name,
                    value_type="binary",
                    adapter_config={
                        "type": "passthrough",
                        "kind": "custom_key_value",
                        "key_id": key_id,
                        "value_id": value_id,
                    },
                    data_provider="publisher",
                )
                repo.add(signal)
                created.append(signal_id)
            else:
                return jsonify({"error": f"unsupported kind: {kind!r}"}), 400

        session.commit()

    return jsonify({"created": len(created), "skipped": skipped, "signal_ids": created})


# ---------------------------------------------------------------------------
# Detail / edit / delete
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Composite builder — rare path for multi-key / OR-groups / exclude signals
# ---------------------------------------------------------------------------


@tenant_signals_bp.route("/composite", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("create_composite_tenant_signal")
def composite_signal(tenant_id: str):
    """Author a signal whose targeting is too complex for the bulk-mapper.

    Embeds the same ``TargetingWidget`` product authoring uses. The
    widget emits a ``key_value_pairs.groups`` payload that we wrap into
    a ``kind="gam_targeting_groups"`` ``adapter_config`` — the GAM
    materializer (#462) routes that shape directly into
    ``_build_groups_custom_targeting_structure``.

    Composite signals are exclusive at buy time — they can't share an
    ``audience_include`` / ``audience_exclude`` list with other signals
    in the same media buy. That's enforced in the materializer; the form
    surfaces it as a callout so operators understand the bound before
    they author.
    """
    with get_db_session() as session:
        gam_repo = GAMSyncRepository(session, tenant_id)
        segments_rows = gam_repo.list_inventory("audience_segment")
        segments = [
            {
                "id": row.inventory_id,
                "name": row.name,
                "type": (row.inventory_metadata or {}).get("type"),
                "size": (row.inventory_metadata or {}).get("size"),
            }
            for row in segments_rows
        ]

    if request.method == "GET":
        return render_template(
            "tenant_signals_composite.html",
            tenant_id=tenant_id,
            segments=segments,
            form_data=None,
            errors=None,
        )

    form_data, errors, parsed = _validate_composite_form(request.form)
    if not errors:
        with get_db_session() as session:
            if session.get(Tenant, tenant_id) is None:
                flash("Tenant not found.", "error")
                return redirect(url_for("core.index"))
            repo = TenantSignalRepository(session, tenant_id)
            parsed["signal_id"] = unique_signal_id(parsed["name"], exists=lambda sid: repo.get_by_id(sid) is not None)
            signal = TenantSignal(tenant_id=tenant_id, **parsed)
            repo.add(signal)
            session.commit()
        flash(f"Composite signal {parsed['signal_id']!r} created.", "success")
        return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

    return render_template(
        "tenant_signals_composite.html",
        tenant_id=tenant_id,
        segments=segments,
        form_data=form_data,
        errors=errors,
    )


def _validate_composite_form(form) -> tuple[dict, dict, dict]:
    """Two composition modes:

    - ``source="audience"`` — operator ticked N audience segments + chose
      include/exclude per row. Emits an AND of audience_segment criteria
      (existing ``type="composed"`` shape from #439).
    - ``source="custom_keys"`` — TargetingWidget groups payload, wraps
      into ``kind="gam_targeting_groups"`` (the #462 shape).
    """
    source = (form.get("composite_source") or "audience").strip()
    form_data = {
        "composite_source": source,
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "audience_picks": form.get("audience_picks") or "",
        "targeting_data": form.get("targeting_data") or "",
    }
    errors: dict[str, str] = {}
    parsed: dict = {}

    if not form_data["name"]:
        errors["name"] = "Name is required (composite signals can't auto-derive a name)."
    else:
        parsed["name"] = form_data["name"]
    parsed["description"] = form_data["description"] or None

    if source == "audience":
        _parse_audience_composition(form_data, errors, parsed)
    elif source == "custom_keys":
        _parse_custom_keys_composition(form_data, errors, parsed)
    else:
        errors["composite_source"] = f"Unknown composite source {source!r}."

    if errors:
        return form_data, errors, parsed

    parsed["value_type"] = "binary"
    parsed["categories"] = []
    parsed["range_min"] = None
    parsed["range_max"] = None
    parsed["data_provider"] = "publisher"
    return form_data, errors, parsed


def _parse_audience_composition(form_data: dict, errors: dict, parsed: dict) -> None:
    """Audience-segment AND builder: list of ``{segment_id, mode}`` picks.
    Two or more picks compose to AND; a single pick is a passthrough."""
    raw = form_data["audience_picks"].strip()
    if not raw:
        errors["audience_picks"] = "Pick at least one audience segment."
        return
    try:
        picks = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        errors["audience_picks"] = f"audience_picks must be JSON: {exc}"
        return
    if not isinstance(picks, list) or not picks:
        errors["audience_picks"] = "Pick at least one audience segment."
        return

    criteria: list[dict] = []
    for pick in picks:
        if not isinstance(pick, dict):
            errors["audience_picks"] = "Each pick must be an object."
            return
        segment_id = str(pick.get("segment_id") or "")
        mode = pick.get("mode", "include")
        if not segment_id:
            errors["audience_picks"] = "Each pick needs a segment_id."
            return
        if mode not in ("include", "exclude"):
            errors["audience_picks"] = f"mode must be include or exclude (got {mode!r})."
            return
        criteria.append({"kind": "audience_segment", "segment_id": segment_id, "mode": mode})

    if len(criteria) == 1:
        parsed["adapter_config"] = {"type": "passthrough", **criteria[0]}
    else:
        parsed["adapter_config"] = {"type": "composed", "criteria": criteria}


def _parse_custom_keys_composition(form_data: dict, errors: dict, parsed: dict) -> None:
    """TargetingWidget groups payload → ``gam_targeting_groups`` adapter
    config. (Existing #462 shape — unchanged.)"""
    raw = form_data["targeting_data"].strip()
    if not raw:
        errors["targeting_data"] = "Build at least one criterion in the targeting builder."
        return

    try:
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        errors["targeting_data"] = f"Targeting builder payload must be JSON: {exc}"
        return

    if not isinstance(payload, dict):
        errors["targeting_data"] = "Targeting builder payload must be a JSON object."
        return

    groups = (payload.get("key_value_pairs") or {}).get("groups") or []
    if not groups:
        errors["targeting_data"] = "Add at least one criterion in the targeting builder."
        return

    for group_idx, group in enumerate(groups):
        criteria = group.get("criteria") or []
        if not criteria:
            errors["targeting_data"] = f"Group {group_idx + 1} has no criteria."
            return
        for crit_idx, criterion in enumerate(criteria):
            if not criterion.get("keyId"):
                errors["targeting_data"] = f"Group {group_idx + 1} criterion {crit_idx + 1} is missing a key."
                return
            if not criterion.get("values"):
                errors["targeting_data"] = f"Group {group_idx + 1} criterion {crit_idx + 1} has no values."
                return

    parsed["adapter_config"] = {
        "type": "passthrough",
        "kind": "gam_targeting_groups",
        "groups": groups,
    }


@tenant_signals_bp.route("/<signal_id>/edit", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("update_tenant_signal")
def edit_signal(tenant_id: str, signal_id: str):
    """Edit name / description / advanced config of an existing signal.

    Detail surface that doubles as edit. Operators reach this via the
    library list. ``signal_id`` is shown here (monospace, copyable) — the
    one place it's legitimately needed for debugging integrations.
    """
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
            return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

        if request.method == "GET":
            mapping_summary = _summarize_adapter_config(
                signal.adapter_config or {}, GAMSyncRepository(session, tenant_id)
            )
            return render_template(
                "tenant_signals_edit.html",
                tenant_id=tenant_id,
                signal=signal,
                mapping_summary=mapping_summary,
                form_data=None,
                errors=None,
                value_types=_VALID_VALUE_TYPES,
            )

        form_data, errors, parsed = _validate_edit_form(request.form)
        if errors:
            return render_template(
                "tenant_signals_edit.html",
                tenant_id=tenant_id,
                signal=signal,
                form_data=form_data,
                errors=errors,
                value_types=_VALID_VALUE_TYPES,
            )
        for field, value in parsed.items():
            setattr(signal, field, value)
        session.commit()
    flash(f"Signal {signal_id!r} updated.", "success")
    return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))


@tenant_signals_bp.route("/<signal_id>/delete", methods=["POST", "DELETE"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("delete_tenant_signal")
def delete_signal(tenant_id: str, signal_id: str):
    with get_db_session() as session:
        repo = TenantSignalRepository(session, tenant_id)
        signal = repo.get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
        else:
            repo.delete(signal)
            session.commit()
            flash(f"Signal {signal_id!r} deleted.", "success")
    return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))


# ---------------------------------------------------------------------------
# Edit-form validation (light — only name / description / advanced JSON)
# ---------------------------------------------------------------------------


def _validate_edit_form(form) -> tuple[dict, dict, dict]:
    """Edit form is light: name (required), description, optional
    advanced-JSON for hand-authored rows. ``signal_id`` is immutable
    (buyer-referenced handle).
    """
    form_data = {
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "value_type": (form.get("value_type") or "").strip(),
        "categories": (form.get("categories") or "").strip(),
        "range_min": (form.get("range_min") or "").strip(),
        "range_max": (form.get("range_max") or "").strip(),
        "targeting_dimension": (form.get("targeting_dimension") or "").strip(),
        "data_provider": (form.get("data_provider") or "").strip(),
        "adapter_config": form.get("adapter_config") or "",
    }
    errors: dict[str, str] = {}
    parsed: dict = {}

    if not form_data["name"]:
        errors["name"] = "Name is required."
    else:
        parsed["name"] = form_data["name"]
    parsed["description"] = form_data["description"] or None
    parsed["targeting_dimension"] = form_data["targeting_dimension"] or None
    parsed["data_provider"] = form_data["data_provider"] or None

    if form_data["value_type"]:
        if form_data["value_type"] not in _VALID_VALUE_TYPES:
            errors["value_type"] = f"value_type must be one of {', '.join(_VALID_VALUE_TYPES)}."
        else:
            parsed["value_type"] = form_data["value_type"]

    if form_data["categories"]:
        parsed["categories"] = _parse_csv(form_data["categories"])

    if form_data["range_min"] or form_data["range_max"]:
        try:
            parsed["range_min"] = _parse_float(form_data["range_min"])
            parsed["range_max"] = _parse_float(form_data["range_max"])
        except ValueError:
            errors["range"] = "range_min and range_max must be numeric or empty."

    if form_data["adapter_config"]:
        try:
            adapter_config = json.loads(form_data["adapter_config"])
            if not isinstance(adapter_config, dict):
                raise ValueError("adapter_config must be a JSON object.")
            parsed["adapter_config"] = adapter_config
        except (ValueError, json.JSONDecodeError) as exc:
            errors["adapter_config"] = f"Invalid JSON: {exc}"

    return form_data, errors, parsed


# ---------------------------------------------------------------------------
# Mapping summary — humanize adapter_config for the edit page
# ---------------------------------------------------------------------------


def _summarize_adapter_config(adapter_config: dict, gam_repo: GAMSyncRepository) -> dict:
    """Render an adapter_config as a human-readable summary.

    Operators editing a signal need to see what it actually maps to in
    GAM without cracking open the JSON. Returns a dict with:

    - ``label``: short summary string (e.g. "GAM audience segment")
    - ``rows``: list of ``{"label": "...", "value": "..."}`` for the
      template to render as a key-value list
    - ``raw_kind``: the underlying ``kind`` (or ``"composed"``) for
      operator-facing badging

    Falls back to ``{"label": "Unknown shape", "rows": []}`` on adapter
    configs we don't recognize — the Advanced JSON section is still
    available as the source of truth.
    """
    config_type = adapter_config.get("type")
    if config_type == "composed":
        criteria = adapter_config.get("criteria") or []
        rows = [
            {"label": f"Criterion {i + 1}", "value": _summarize_criterion(c, gam_repo)} for i, c in enumerate(criteria)
        ]
        return {
            "label": f"Composed AND of {len(criteria)} criteria",
            "rows": rows,
            "raw_kind": "composed",
        }

    kind = adapter_config.get("kind")
    if kind == "gam_targeting_groups":
        groups = adapter_config.get("groups") or []
        crit_count = sum(len(g.get("criteria") or []) for g in groups)
        return {
            "label": f"Composite GAM targeting — {len(groups)} group(s), {crit_count} criterion(a)",
            "rows": _summarize_groups(groups, gam_repo),
            "raw_kind": "gam_targeting_groups",
        }

    # Pass-through (legacy + explicit)
    if kind == "audience_segment":
        segment_id = str(adapter_config.get("segment_id") or "")
        seg_name = _lookup_inventory_name(gam_repo, "audience_segment", segment_id)
        return {
            "label": "GAM audience segment",
            "rows": [
                {"label": "Segment", "value": f"{seg_name or '(unsynced)'} — id {segment_id}"},
            ],
            "raw_kind": kind,
        }
    if kind == "custom_key_value":
        key_id = str(adapter_config.get("key_id") or "")
        value_id = str(adapter_config.get("value_id") or "")
        key_name = _lookup_inventory_name(gam_repo, "custom_targeting_key", key_id)
        return {
            "label": "GAM custom key + value",
            "rows": [
                {"label": "Key", "value": f"{key_name or '(unsynced)'} — id {key_id}"},
                {"label": "Value id", "value": value_id},
            ],
            "raw_kind": kind,
        }
    if kind in ("freewheel_viewership_profile", "freewheel_audience_item", "freewheel_custom_kv"):
        return {
            "label": f"Freewheel {kind.replace('freewheel_', '').replace('_', ' ')}",
            "rows": [
                {"label": "Config", "value": ", ".join(f"{k}={v}" for k, v in adapter_config.items() if k != "type")}
            ],
            "raw_kind": kind,
        }

    return {"label": "Unknown adapter shape — edit JSON directly to update", "rows": [], "raw_kind": kind or "unknown"}


def _summarize_criterion(criterion: dict, gam_repo: GAMSyncRepository) -> str:
    """One-line summary of one composed-criterion dict."""
    kind = criterion.get("kind")
    mode = criterion.get("mode", "include")
    mode_prefix = "EXCLUDE " if mode == "exclude" else ""
    if kind == "audience_segment":
        segment_id = str(criterion.get("segment_id") or "")
        seg_name = _lookup_inventory_name(gam_repo, "audience_segment", segment_id)
        return f"{mode_prefix}audience segment {seg_name or segment_id}"
    if kind == "custom_key_value":
        key_id = str(criterion.get("key_id") or "")
        value_id = str(criterion.get("value_id") or "")
        key_name = _lookup_inventory_name(gam_repo, "custom_targeting_key", key_id) or key_id
        return f"{mode_prefix}{key_name}={value_id}"
    return f"{mode_prefix}{kind}"


def _summarize_groups(groups: list[dict], gam_repo: GAMSyncRepository) -> list[dict]:
    """Render the TargetingWidget groups payload as a list of rows."""
    rows: list[dict] = []
    for g_idx, group in enumerate(groups):
        criteria = group.get("criteria") or []
        parts = []
        for crit in criteria:
            key_id = str(crit.get("keyId") or "")
            values = crit.get("values") or []
            exclude = crit.get("exclude")
            key_name = _lookup_inventory_name(gam_repo, "custom_targeting_key", key_id) or key_id
            op = "NOT IN" if exclude else "IN"
            parts.append(f"{key_name} {op} [{', '.join(str(v) for v in values)}]")
        rows.append({"label": f"Group {g_idx + 1}", "value": " AND ".join(parts) or "(empty)"})
    return rows


def _lookup_inventory_name(gam_repo: GAMSyncRepository, inventory_type: str, inventory_id: str) -> str | None:
    """Look up the human display name for a GAM inventory row.
    Returns ``None`` when the row isn't in synced inventory (operator hasn't
    synced yet, or the row has been removed in GAM).
    """
    if not inventory_id:
        return None
    for row in gam_repo.list_inventory(inventory_type):
        if row.inventory_id == inventory_id:
            return row.name
    return None
