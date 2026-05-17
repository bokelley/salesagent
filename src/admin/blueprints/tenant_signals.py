"""Admin blueprint for managing tenant signals.

Tenant signals are operator-declared adapter targeting capabilities — the
publisher's first-party map of "what targeting can a buyer apply on this
inventory." They surface to the storefront through the AdCP ``get_signals``
tool with their public schema (``value_type`` / ``categories`` / ``range``);
the adapter-specific ``adapter_config`` resolution map stays operator-side.

This UI is the operator's authoring surface. The same data is reachable via
REST at ``/api/v1/tenants/<id>/signals`` for programmatic operators.

Authoring model:

    Operator picks a *source* (GAM audience segment / GAM custom key+value /
    Freewheel viewership profile / Freewheel audience item / Freewheel
    custom key+value), then picks one or more *entities* from the chosen
    source (looked up live from the ad server's discovery API). The form
    derives ``signal_id`` from the operator's name, ``value_type`` from the
    source, and the resolution ``adapter_config`` from the picked entities.
    A FREEFORM GAM key is the only case that asks for operator-supplied
    values — by definition GAM has nothing to enumerate.

The legacy "Advanced (composed / JSON)" textarea is preserved on the edit
form so operators can hand-author composed expressions or shapes the
picker doesn't yet support (multi-adapter compositions). New rows always
flow through the structured pickers.
"""

from __future__ import annotations

import json
import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.admin.utils.signal_id import unique_signal_id
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantSignal
from src.core.database.repositories.tenant_signal import TenantSignalRepository

logger = logging.getLogger(__name__)

tenant_signals_bp = Blueprint("tenant_signals", __name__)

_VALID_VALUE_TYPES = ("binary", "categorical", "numeric")

# Source kinds the structured picker emits. Each maps to a specific
# adapter_config shape; the picker UI hides the JSON entirely.
_SOURCE_KINDS = {
    "gam_audience_segment",  # one or more GAM audience segments (AND)
    "gam_custom_key_value",  # one or more (key, value) pairs (AND)
    "fw_viewership_profile",
    "fw_audience_item",
    "fw_custom_kv",
}


def _parse_csv(raw: str | None) -> list[str]:
    """Parse a comma-separated form field into a clean list of strings."""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _parse_json(raw: str | None, *, default):
    """Parse a JSON form field. Empty input → ``default``."""
    if not raw or not str(raw).strip():
        return default
    return json.loads(raw)


def _parse_float(raw: str | None) -> float | None:
    if raw is None or str(raw).strip() == "":
        return None
    return float(raw)


@tenant_signals_bp.route("/")
@require_tenant_access()
def list_signals(tenant_id: str):
    """List operator-declared signals for a tenant."""
    with get_db_session() as session:
        tenant = session.get(Tenant, tenant_id)
        if tenant is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))
        rows = TenantSignalRepository(session, tenant_id).list_all()
        signals = [
            {
                "signal_id": row.signal_id,
                "name": row.name,
                "description": row.description,
                "value_type": row.value_type,
                "categories": row.categories or [],
                "range_min": row.range_min,
                "range_max": row.range_max,
                "targeting_dimension": row.targeting_dimension,
                "data_provider": row.data_provider,
                "adapter_kind": (row.adapter_config or {}).get("kind"),
                "updated_at": row.updated_at,
            }
            for row in rows
        ]
    return render_template(
        "tenant_signals_list.html",
        tenant_id=tenant_id,
        tenant_name=tenant.name,
        signals=signals,
    )


@tenant_signals_bp.route("/add", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("create_tenant_signal")
def add_signal(tenant_id: str):
    if request.method == "GET":
        return render_template(
            "tenant_signals_form.html",
            tenant_id=tenant_id,
            mode="add",
            signal=None,
            form_data=None,
            errors=None,
            value_types=_VALID_VALUE_TYPES,
        )

    form_data, errors, parsed = _validate_form(request.form, mode="add")
    if not errors:
        with get_db_session() as session:
            if session.get(Tenant, tenant_id) is None:
                flash("Tenant not found.", "error")
                return redirect(url_for("core.index"))
            repo = TenantSignalRepository(session, tenant_id)
            # Auto-generate signal_id from name; disambiguate against the
            # tenant's existing row ids.
            parsed["signal_id"] = unique_signal_id(parsed["name"], exists=lambda sid: repo.get_by_id(sid) is not None)
            signal = TenantSignal(tenant_id=tenant_id, **parsed)
            repo.add(signal)
            session.commit()
        flash(f"Signal {parsed['signal_id']!r} created.", "success")
        return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

    return render_template(
        "tenant_signals_form.html",
        tenant_id=tenant_id,
        mode="add",
        signal=None,
        form_data=form_data,
        errors=errors,
        value_types=_VALID_VALUE_TYPES,
    )


@tenant_signals_bp.route("/<signal_id>/edit", methods=["GET", "POST"])
@require_tenant_access(role=("admin", "member"), allow_embedded_writes=True)
@log_admin_action("update_tenant_signal")
def edit_signal(tenant_id: str, signal_id: str):
    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))
        signal = TenantSignalRepository(session, tenant_id).get_by_id(signal_id)
        if signal is None:
            flash(f"Signal {signal_id!r} not found.", "error")
            return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))

        if request.method == "GET":
            return render_template(
                "tenant_signals_form.html",
                tenant_id=tenant_id,
                mode="edit",
                signal=signal,
                form_data=None,
                errors=None,
                value_types=_VALID_VALUE_TYPES,
            )

        # mode="edit" — signal_id is immutable (it's a public handle buyers
        # may already reference). Operator-edits flow through the same
        # source-first picker; name changes do NOT re-slug.
        form_data, errors, parsed = _validate_form(request.form, mode="edit")
        if errors:
            return render_template(
                "tenant_signals_form.html",
                tenant_id=tenant_id,
                mode="edit",
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
# Form validation
# ---------------------------------------------------------------------------


def _validate_form(form, *, mode: str) -> tuple[dict, dict, dict]:
    """Validate form input and return (form_data_for_re-render, errors, parsed_kwargs).

    Two authoring paths supported:

    - **Structured (default)**: ``source_kind`` + per-source entity fields.
      The picker UI emits these. value_type, categories, and adapter_config
      are derived; signal_id is generated server-side from ``name``.
    - **Advanced JSON (escape hatch)**: ``authoring_mode == "advanced"``
      passes raw ``adapter_config`` JSON + manual ``value_type`` /
      ``categories`` / range. Available on edit only so existing
      hand-authored rows can still round-trip.
    """
    authoring_mode = (form.get("authoring_mode") or "structured").strip()
    form_data = {
        "authoring_mode": authoring_mode,
        "name": (form.get("name") or "").strip(),
        "description": (form.get("description") or "").strip(),
        "targeting_dimension": (form.get("targeting_dimension") or "").strip(),
        "data_provider": (form.get("data_provider") or "").strip(),
        # Structured-mode fields
        "source_kind": (form.get("source_kind") or "").strip(),
        "entities": form.get("entities") or "",
        "freeform_values": (form.get("freeform_values") or "").strip(),
        # Advanced-mode fields
        "value_type": (form.get("value_type") or "").strip(),
        "categories": (form.get("categories") or "").strip(),
        "range_min": (form.get("range_min") or "").strip(),
        "range_max": (form.get("range_max") or "").strip(),
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

    if authoring_mode == "advanced":
        _parse_advanced_fields(form_data, errors, parsed)
    else:
        _parse_structured_fields(form_data, errors, parsed)

    return form_data, errors, parsed


def _parse_structured_fields(form_data: dict, errors: dict, parsed: dict) -> None:
    """Translate source-first picker fields into TenantSignal kwargs.

    The picker emits ``source_kind`` (one of the values in
    ``_SOURCE_KINDS``) and ``entities`` — a JSON array of source-specific
    objects (e.g. ``[{"segment_id": "..."}, ...]`` or
    ``[{"key_id": "...", "value_id": "...", "key_type": "PREDEFINED"}]``).
    A single entity emits a pass-through ``adapter_config``; multiple
    entities emit a composed AND.

    FREEFORM custom keys may be paired with operator-supplied
    ``freeform_values`` (CSV) — those become the public ``categories``
    list since GAM has nothing to enumerate. PREDEFINED keys leave
    ``categories`` empty; values resolve live at targeting time.
    """
    source_kind = form_data["source_kind"]
    if not source_kind:
        errors["source_kind"] = "Pick a source for this signal."
        return
    if source_kind not in _SOURCE_KINDS:
        errors["source_kind"] = f"Unknown source kind {source_kind!r}."
        return

    try:
        entities = _parse_json(form_data["entities"], default=[])
    except (ValueError, json.JSONDecodeError) as exc:
        errors["entities"] = f"entities must be a JSON array: {exc}"
        return
    if not isinstance(entities, list) or not entities:
        errors["entities"] = "Pick at least one entity for the signal."
        return

    try:
        criteria = [_entity_to_criterion(source_kind, ent) for ent in entities]
    except ValueError as exc:
        errors["entities"] = str(exc)
        return

    # Single entity → pass-through; multiple → composed AND. Both shapes are
    # already understood by every per-adapter materializer (#439).
    if len(criteria) == 1:
        parsed["adapter_config"] = {"type": "passthrough", **criteria[0]}
    else:
        parsed["adapter_config"] = {"type": "composed", "criteria": criteria}

    parsed["value_type"] = "binary"
    parsed["categories"] = _parse_csv(form_data["freeform_values"])
    parsed["range_min"] = None
    parsed["range_max"] = None


def _entity_to_criterion(source_kind: str, entity: dict) -> dict:
    """Normalize one picker-emitted entity to a materializer criterion.

    Raises ``ValueError`` with an operator-friendly message if the entity
    is missing required ids — the picker should never emit these but we
    validate defensively (the field is JSON the operator could craft).
    """
    if not isinstance(entity, dict):
        raise ValueError("Each entity must be an object.")
    mode = entity.get("mode", "include")
    if mode not in ("include", "exclude"):
        raise ValueError(f"mode must be 'include' or 'exclude' (got {mode!r}).")

    if source_kind == "gam_audience_segment":
        segment_id = entity.get("segment_id")
        if not segment_id:
            raise ValueError("GAM audience segment requires segment_id.")
        return {"kind": "audience_segment", "segment_id": str(segment_id), "mode": mode}

    if source_kind == "gam_custom_key_value":
        key_id, value_id = entity.get("key_id"), entity.get("value_id")
        if not key_id or not value_id:
            raise ValueError("GAM custom key+value requires key_id and value_id.")
        return {
            "kind": "custom_key_value",
            "key_id": str(key_id),
            "value_id": str(value_id),
            "mode": mode,
        }

    if source_kind == "fw_viewership_profile":
        profile_id = entity.get("profile_id")
        if profile_id is None:
            raise ValueError("Freewheel viewership profile requires profile_id.")
        return {"kind": "freewheel_viewership_profile", "profile_id": int(profile_id), "mode": mode}

    if source_kind == "fw_audience_item":
        item_id = entity.get("item_id")
        if item_id is None:
            raise ValueError("Freewheel audience item requires item_id.")
        return {"kind": "freewheel_audience_item", "item_id": int(item_id), "mode": mode}

    if source_kind == "fw_custom_kv":
        key, value_id = entity.get("key"), entity.get("value_id")
        if not key or not value_id:
            raise ValueError("Freewheel custom KV requires key and value_id.")
        return {"kind": "freewheel_custom_kv", "key": str(key), "value_id": str(value_id), "mode": mode}

    raise ValueError(f"Unsupported source_kind {source_kind!r}.")


def _parse_advanced_fields(form_data: dict, errors: dict, parsed: dict) -> None:
    """Hand-authored JSON path — preserved for round-tripping legacy rows."""
    if form_data["value_type"] not in _VALID_VALUE_TYPES:
        errors["value_type"] = f"value_type must be one of {', '.join(_VALID_VALUE_TYPES)}."
    else:
        parsed["value_type"] = form_data["value_type"]

    parsed["categories"] = _parse_csv(form_data["categories"])

    try:
        parsed["range_min"] = _parse_float(form_data["range_min"])
        parsed["range_max"] = _parse_float(form_data["range_max"])
    except ValueError:
        errors["range"] = "range_min and range_max must be numeric or empty."

    try:
        adapter_config = _parse_json(form_data["adapter_config"], default={})
        if not isinstance(adapter_config, dict):
            raise ValueError("adapter_config must be a JSON object.")
        parsed["adapter_config"] = adapter_config
    except (ValueError, json.JSONDecodeError) as exc:
        errors["adapter_config"] = f"Invalid JSON: {exc}"
