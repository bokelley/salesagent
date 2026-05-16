"""Admin blueprint for managing tenant signals.

Tenant signals are operator-declared adapter targeting capabilities — the
publisher's first-party map of "what targeting can a buyer apply on this
inventory." They surface to the storefront through the AdCP ``get_signals``
tool with their public schema (``value_type`` / ``categories`` / ``range``);
the adapter-specific ``adapter_config`` resolution map stays operator-side.

This UI is the operator's authoring surface. The same data is reachable via
REST at ``/api/v1/tenants/<id>/signals`` for programmatic operators.

v1 renders ``adapter_config`` as a JSON textarea with per-adapter examples
in the form. v2 would add adapter-specific pickers (GAM custom-KV key
dropdown, GAM audience segment picker, etc.) once those sync flows are
wired into the admin UI.
"""

from __future__ import annotations

import json
import logging
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for

from src.admin.utils import require_tenant_access
from src.admin.utils.audit_decorator import log_admin_action
from src.core.database.database_session import get_db_session
from src.core.database.models import Tenant, TenantSignal
from src.core.database.repositories.tenant_signal import TenantSignalRepository

logger = logging.getLogger(__name__)

tenant_signals_bp = Blueprint("tenant_signals", __name__)

_VALID_VALUE_TYPES = ("binary", "categorical", "numeric")
# AdCP Signal.signal_id.id pattern — applies here so wire and storage stay
# in sync (no need for the dot-to-underscore sanitization in get_signals
# when authoring goes through this surface).
_SIGNAL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def _parse_csv(raw: str | None) -> list[str]:
    """Parse a comma-separated form field into a clean list of strings."""
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def _parse_adapter_config(raw: str | None) -> dict:
    """Parse the adapter_config JSON textarea. Empty input → empty dict."""
    if not raw or not raw.strip():
        return {}
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
    if errors:
        return render_template(
            "tenant_signals_form.html",
            tenant_id=tenant_id,
            mode="add",
            signal=None,
            form_data=form_data,
            errors=errors,
            value_types=_VALID_VALUE_TYPES,
        )

    with get_db_session() as session:
        if session.get(Tenant, tenant_id) is None:
            flash("Tenant not found.", "error")
            return redirect(url_for("core.index"))
        repo = TenantSignalRepository(session, tenant_id)
        if repo.get_by_id(parsed["signal_id"]) is not None:
            errors = {"signal_id": "A signal with that id already exists."}
            return render_template(
                "tenant_signals_form.html",
                tenant_id=tenant_id,
                mode="add",
                signal=None,
                form_data=form_data,
                errors=errors,
                value_types=_VALID_VALUE_TYPES,
            )
        signal = TenantSignal(tenant_id=tenant_id, **parsed)
        repo.add(signal)
        session.commit()
    flash(f"Signal {parsed['signal_id']!r} created.", "success")
    return redirect(url_for("tenant_signals.list_signals", tenant_id=tenant_id))


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

        # mode="edit" — signal_id is immutable
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
        # Apply the parsed values (signal_id not in parsed for edit mode)
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

    ``parsed_kwargs`` is keyword-arg-shaped for direct splat into ``TenantSignal(...)``
    on create, or attribute-assignment on edit. ``signal_id`` is included on
    create but omitted on edit (immutable).
    """
    form_data = {
        "signal_id": (form.get("signal_id") or "").strip(),
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

    if mode == "add":
        if not form_data["signal_id"]:
            errors["signal_id"] = "Signal id is required."
        elif not _SIGNAL_ID_PATTERN.match(form_data["signal_id"]):
            errors["signal_id"] = "Signal id must match ^[a-zA-Z0-9_-]+$ — AdCP wire constraint."
        else:
            parsed["signal_id"] = form_data["signal_id"]

    if not form_data["name"]:
        errors["name"] = "Name is required."
    else:
        parsed["name"] = form_data["name"]
    parsed["description"] = form_data["description"] or None

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

    parsed["targeting_dimension"] = form_data["targeting_dimension"] or None
    parsed["data_provider"] = form_data["data_provider"] or None

    try:
        adapter_config = _parse_adapter_config(form_data["adapter_config"])
        if not isinstance(adapter_config, dict):
            raise ValueError("adapter_config must be a JSON object.")
        parsed["adapter_config"] = adapter_config
    except (ValueError, json.JSONDecodeError) as exc:
        errors["adapter_config"] = f"Invalid JSON: {exc}"

    return form_data, errors, parsed
