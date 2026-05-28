#!/usr/bin/env python3
"""Generate GAM key-value coverage guidance for signal-like targeting."""

from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.ops.gam_placement_country_price_guidance import (
    _csv_values,
    _get_gam_client_from_args,
    _line_item_types,
    _load_env_file,
    _network_timezone,
    _write_output,
)
from src.adapters.gam_reporting_service import GAMReportingService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", help="Tenant ID with a configured Google Ad Manager adapter.")
    parser.add_argument("--network-code", help="Direct GAM network code. If omitted, the script detects it.")
    parser.add_argument("--refresh-token-env", help="Environment variable containing the GAM OAuth refresh token.")
    parser.add_argument("--refresh-token-file", help="Local file containing the GAM OAuth refresh token.")
    parser.add_argument("--env-file", default=".env", help="Optional env file to load. Defaults to .env.")
    parser.add_argument("--key-name", help="GAM custom targeting key name, e.g. weather.")
    parser.add_argument("--key-id", help="GAM custom targeting key ID. Alternative to --key-name.")
    parser.add_argument(
        "--values", help="Optional comma-separated values to include. Defaults to all registered values."
    )
    parser.add_argument(
        "--date-range",
        choices=["today", "this_month", "lifetime"],
        default="this_month",
        help="Historical reporting window. 'lifetime' is capped by the service's existing safety window.",
    )
    parser.add_argument(
        "--line-item-types",
        default="PRICE_PRIORITY",
        help="Comma-separated GAM line item types to include. Defaults to PRICE_PRIORITY. Use 'all' to disable.",
    )
    parser.add_argument(
        "--min-value-impressions",
        type=int,
        default=1,
        help="Minimum impressions required for a value bucket to be included. Defaults to 1.",
    )
    parser.add_argument("--output", help="Optional JSON output path. Defaults to stdout.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    if not args.key_name and not args.key_id:
        raise ValueError("Pass --key-name or --key-id")
    if args.key_name and args.key_id:
        raise ValueError("Use either --key-name or --key-id, not both")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_env_file(args.env_file)
    gam_client = _get_gam_client_from_args(
        tenant_id=args.tenant_id,
        network_code=args.network_code,
        refresh_token_env=args.refresh_token_env,
        refresh_token_file=args.refresh_token_file,
    )
    timezone = _network_timezone(gam_client)
    service = GAMReportingService(gam_client, timezone)
    payload = service.get_custom_targeting_value_coverage(
        args.date_range,
        key_name=args.key_name,
        key_id=args.key_id,
        value_names=_csv_values(args.values),
        line_item_types=_line_item_types(args.line_item_types),
        min_value_impressions=args.min_value_impressions,
        requested_timezone=timezone,
    )
    _write_output(payload, args.output)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from None
