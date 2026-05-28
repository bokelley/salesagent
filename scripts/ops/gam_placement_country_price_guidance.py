#!/usr/bin/env python3
"""Generate GAM placement-country price guidance from line-item delivery.

This is a read-only prototype. It runs a GAM historical report at
placement x country x line-item grain, then computes impression-weighted
CPM percentiles suitable for product pricing guidance.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.auth.exceptions import RefreshError
from googleads import ad_manager

from src.adapters.gam.auth import GAMAuthManager
from src.adapters.gam.client import GAMClientManager
from src.adapters.gam_reporting_service import GAMReportingService

logger = logging.getLogger(__name__)
DEFAULT_REFRESH_TOKEN_FILE = ".context/gam-refresh-token"
DEFAULT_MAX_NETWORK_LINE_ITEMS = 600_000


def _load_env_file(env_file: str | None) -> None:
    if not env_file:
        return
    path = Path(env_file)
    if not path.exists():
        raise FileNotFoundError(f"Environment file not found: {path}")

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        os.environ.setdefault(key, value)


def _csv_values(value: str | None) -> list[str] | None:
    if value is None:
        return None
    values = [part.strip() for part in value.split(",") if part.strip()]
    return values or None


def _line_item_types(value: str) -> list[str] | None:
    if value.strip().lower() == "all":
        return []
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def _write_output(payload: dict[str, Any], output_path: str | None) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True)
    if output_path is None:
        print(body)
        return

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n")
    logger.info("Wrote price guidance report to %s", path)


def _get_gam_client_for_tenant(tenant_id: str):
    from src.core.database.database_session import get_db_session
    from src.core.database.repositories.adapter_config import AdapterConfigRepository

    with get_db_session() as session:
        repo = AdapterConfigRepository(session, tenant_id)
        adapter_config = repo.get_by_tenant()
        gam_config = repo.get_gam_config(adapter_config)
        network_code = adapter_config.gam_network_code
        if not network_code:
            raise ValueError(f"GAM network code is not configured for tenant {tenant_id!r}")

    return GAMClientManager(gam_config, network_code).get_client()


def _object_field(obj: Any, field: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(field)
    return getattr(obj, field, None)


def _refresh_token_from_args(refresh_token_env: str | None, refresh_token_file: str | None) -> str:
    if refresh_token_env and refresh_token_file:
        raise ValueError("Use either --refresh-token-env or --refresh-token-file, not both")
    if refresh_token_file:
        token = _read_refresh_token_file(Path(refresh_token_file))
        if not token:
            raise ValueError(f"Refresh token file is empty: {refresh_token_file}")
        return token

    default_path = Path(DEFAULT_REFRESH_TOKEN_FILE)
    if default_path.exists():
        token = _read_refresh_token_file(default_path)
        if not token:
            raise ValueError(f"Refresh token file is empty: {default_path}")
        return token

    env_name = refresh_token_env or "GAM_REFRESH_TOKEN"
    token = os.environ.get(env_name)
    if not token:
        raise ValueError(
            f"Refresh token environment variable {env_name!r} is not set and {DEFAULT_REFRESH_TOKEN_FILE} was not found"
        )
    return token


def _read_refresh_token_file(path: Path) -> str:
    text = path.read_text().strip()
    if text.startswith("GAM_REFRESH_TOKEN="):
        return text.split("=", 1)[1].strip().strip("'\"")
    return text


def _detect_networks(refresh_token: str) -> list[dict[str, Any]]:
    credentials = GAMAuthManager({"refresh_token": refresh_token}).get_credentials()
    client = ad_manager.AdManagerClient(credentials, "Prebid Sales Agent")
    network_service = client.GetService("NetworkService")
    try:
        networks = network_service.getAllNetworks() or []
    except RefreshError as exc:
        raise RuntimeError(
            "OAuth refresh failed before GAM network detection. The refresh token may be expired/revoked, "
            "or it may have been minted with a different GAM_OAUTH_CLIENT_ID/GAM_OAUTH_CLIENT_SECRET pair."
        ) from exc
    return [
        {
            "network_code": str(_object_field(network, "networkCode") or ""),
            "display_name": str(_object_field(network, "displayName") or ""),
            "currency_code": str(_object_field(network, "currencyCode") or ""),
            "time_zone": str(_object_field(network, "timeZone") or ""),
            "is_test": bool(_object_field(network, "isTest") or False),
        }
        for network in networks
    ]


def _network_code_from_refresh_token(refresh_token: str) -> str:
    networks = _detect_networks(refresh_token)
    if not networks:
        raise ValueError("The refresh token has no accessible GAM networks")
    if len(networks) > 1:
        network_lines = "\n".join(
            f"  - {network['network_code']}: {network['display_name']} "
            f"({network['currency_code']}, {network['time_zone']})"
            for network in networks
        )
        raise ValueError(
            f"The refresh token has access to multiple GAM networks; pass --network-code:\n{network_lines}"
        )
    network_code = networks[0]["network_code"]
    if not network_code:
        raise ValueError("GAM returned one network but no networkCode")
    logger.info("Detected GAM network %s (%s)", network_code, networks[0]["display_name"])
    return network_code


def _get_gam_client_from_args(
    *,
    tenant_id: str | None,
    network_code: str | None,
    refresh_token_env: str | None,
    refresh_token_file: str | None,
):
    if tenant_id:
        if network_code or refresh_token_env or refresh_token_file:
            raise ValueError("Use either --tenant-id or direct credential args, not both")
        return _get_gam_client_for_tenant(tenant_id)

    refresh_token = _refresh_token_from_args(refresh_token_env, refresh_token_file)
    if not network_code:
        network_code = _network_code_from_refresh_token(refresh_token)
    return GAMClientManager({"refresh_token": refresh_token}, network_code).get_client()


def _network_timezone(gam_client) -> str:
    try:
        network_service = gam_client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()
        return str(_object_field(network, "timeZone") or "America/New_York")
    except Exception:
        logger.warning("Unable to fetch GAM network timezone; falling back to America/New_York", exc_info=True)
        return "America/New_York"


def _network_currency(gam_client) -> str:
    try:
        network_service = gam_client.GetService("NetworkService")
        network = network_service.getCurrentNetwork()
        return str(_object_field(network, "currencyCode") or "USD")
    except Exception:
        logger.warning("Unable to fetch GAM network currency; falling back to USD", exc_info=True)
        return "USD"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant-id",
        help="Tenant ID with a configured Google Ad Manager adapter. Mutually exclusive with direct credential args.",
    )
    parser.add_argument(
        "--network-code",
        help="Direct GAM network code for one-off read-only reporting. If omitted, the script detects it.",
    )
    parser.add_argument(
        "--refresh-token-env",
        help="Environment variable containing the GAM OAuth refresh token. Defaults to GAM_REFRESH_TOKEN.",
    )
    parser.add_argument(
        "--refresh-token-file",
        help=f"Local file containing the GAM OAuth refresh token. Defaults to {DEFAULT_REFRESH_TOKEN_FILE} if present.",
    )
    parser.add_argument(
        "--list-networks",
        action="store_true",
        help="List GAM networks available to the refresh token and exit. Does not run reports.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional env file to load before building the GAM client. Defaults to .env.",
    )
    parser.add_argument(
        "--date-range",
        choices=["today", "this_month", "lifetime"],
        default="this_month",
        help="Historical reporting window. 'lifetime' is capped by the service's existing safety window.",
    )
    parser.add_argument(
        "--placement-ids",
        help="Optional comma-separated GAM placement IDs. Use this to batch large networks.",
    )
    parser.add_argument(
        "--countries",
        help="Optional comma-separated GAM country names, e.g. 'United States,Canada'.",
    )
    parser.add_argument(
        "--line-item-types",
        default="PRICE_PRIORITY",
        help="Comma-separated GAM line item types to include. Defaults to PRICE_PRIORITY. Use 'all' to disable.",
    )
    parser.add_argument(
        "--min-group-impressions",
        type=int,
        default=10_000,
        help="Minimum placement-country impressions required in the output.",
    )
    parser.add_argument(
        "--min-line-item-impressions",
        type=int,
        default=1_000,
        help="Minimum line-item impressions required before that row contributes to guidance.",
    )
    parser.add_argument(
        "--include-zero-revenue",
        action="store_true",
        help="Include zero-revenue line items in percentiles. Default excludes them for pricing guidance.",
    )
    parser.add_argument(
        "--min-package-budget",
        type=float,
        help=(
            "Minimum package budget for bookability gating. Defaults to the capacity-derived minimum package budget."
        ),
    )
    parser.add_argument(
        "--bookability-safety-factor",
        type=float,
        default=1.0,
        help="Multiplier applied to required units when checking minimum-budget bookability. Defaults to 1.0.",
    )
    parser.add_argument(
        "--publisher-domain",
        help="Optional publisher domain to include in placement dimension refs.",
    )
    parser.add_argument(
        "--currency",
        help="Currency code for the emitted forecast envelope. Defaults to the GAM network currency.",
    )
    parser.add_argument(
        "--viewability-standard",
        default="mrc",
        help="Viewability standard label for forecast point viewability metrics. Defaults to mrc.",
    )
    parser.add_argument(
        "--max-network-line-items",
        type=int,
        default=DEFAULT_MAX_NETWORK_LINE_ITEMS,
        help=(
            "Assumed GAM total line-item cap for capacity guidance. "
            f"Defaults to Google's published total line-item limit, {DEFAULT_MAX_NETWORK_LINE_ITEMS}."
        ),
    )
    parser.add_argument(
        "--capacity-only",
        action="store_true",
        help="Only compute revenue and line-item capacity guidance; skip the placement-country price report.",
    )
    parser.add_argument(
        "--line-item-space-fraction",
        type=float,
        default=0.01,
        help="Monthly share of total GAM line-item space Sales Agent should consume. Defaults to 0.01.",
    )
    parser.add_argument(
        "--line-items-per-package",
        type=int,
        default=1,
        help="Estimated GAM line items created per sellable package. Defaults to 1.",
    )
    parser.add_argument("--output", help="Optional JSON output path. Defaults to stdout.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_env_file(args.env_file)
    if args.list_networks:
        if args.tenant_id:
            raise ValueError("--list-networks is only supported with direct refresh-token credentials")
        refresh_token = _refresh_token_from_args(args.refresh_token_env, args.refresh_token_file)
        _write_output({"networks": _detect_networks(refresh_token)}, args.output)
        return

    gam_client = _get_gam_client_from_args(
        tenant_id=args.tenant_id,
        network_code=args.network_code,
        refresh_token_env=args.refresh_token_env,
        refresh_token_file=args.refresh_token_file,
    )
    timezone = _network_timezone(gam_client)
    currency = args.currency or _network_currency(gam_client)
    service = GAMReportingService(gam_client, timezone)
    capacity_guidance = service.get_line_item_capacity_guidance(
        "this_month",
        max_network_line_items=args.max_network_line_items,
        monthly_line_item_space_fraction=args.line_item_space_fraction,
        estimated_line_items_per_package=args.line_items_per_package,
        requested_timezone=timezone,
    )
    min_package_budget = (
        args.min_package_budget
        if args.min_package_budget is not None
        else capacity_guidance.get("minimum_package_budget")
    )
    if args.capacity_only:
        payload = {}
    else:
        payload = service.get_placement_country_price_guidance(
            args.date_range,
            placement_ids=_csv_values(args.placement_ids),
            countries=_csv_values(args.countries),
            line_item_types=_line_item_types(args.line_item_types),
            min_group_impressions=args.min_group_impressions,
            min_line_item_impressions=args.min_line_item_impressions,
            min_package_budget=min_package_budget,
            bookability_safety_factor=args.bookability_safety_factor,
            include_zero_revenue=args.include_zero_revenue,
            currency=currency,
            publisher_domain=args.publisher_domain,
            viewability_standard=args.viewability_standard,
            requested_timezone=timezone,
        )
    payload["line_item_capacity_guidance"] = capacity_guidance
    payload["effective_min_package_budget"] = min_package_budget
    _write_output(payload, args.output)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from None
