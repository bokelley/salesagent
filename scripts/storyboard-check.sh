#!/usr/bin/env bash
# Run the @adcp/sdk media_buy_seller storyboard against a live agent on both
# transports (MCP + A2A) and print a tight pass/fail summary.
#
# This is the launch-readiness check that would have caught #71. The reporter
# ran this exact storyboard against deployed Wonderstruck staging — every
# protocol drift surfaces here in seconds.
#
# Usage:
#   AGENT_URL=https://wonderstruck.sales-agent.scope3.com \
#   AGENT_TOKEN=<bearer-token> \
#   ./scripts/storyboard-check.sh
#
#   # Or pass storyboard ID (default: media_buy_seller)
#   STORYBOARD=capability_discovery ./scripts/storyboard-check.sh
#
#   # Or production-path mode (no sandbox routing):
#   NO_SANDBOX=1 ./scripts/storyboard-check.sh
#
#   # Local dev (non-HTTPS agent — production must terminate TLS):
#   AGENT_URL=http://localhost:8000 AGENT_TOKEN=ci-test-token \
#   ALLOW_HTTP=1 ./scripts/storyboard-check.sh
#
# Outputs:
#   - One-line summary per transport: passed/failed/skipped + duration
#   - List of failing step IDs with truncated error messages
#   - Per-transport JSON written to /tmp/storyboard-<protocol>.json for
#     drilldown
#
# Exit code:
#   0 if BOTH transports pass all steps
#   1 if either transport has any failing step
#   2 on missing prerequisites (npx, jq, env vars)
#
# Prereqs: npx (Node), jq

set -uo pipefail

# ─── Args / env ──────────────────────────────────────────────────────────────
AGENT_URL="${AGENT_URL:-}"
AGENT_TOKEN="${AGENT_TOKEN:-}"
STORYBOARD="${STORYBOARD:-media_buy_seller}"
TIMEOUT="${TIMEOUT:-180}"
NO_SANDBOX="${NO_SANDBOX:-0}"
# ALLOW_HTTP="1" lets you run against a non-HTTPS local agent (e.g.
# http://localhost:8000). The SDK refuses HTTP by default — production
# agents must terminate TLS. Only use this for local dev validation.
ALLOW_HTTP="${ALLOW_HTTP:-0}"

if [[ -z "$AGENT_URL" || -z "$AGENT_TOKEN" ]]; then
    cat <<EOF >&2
✗ AGENT_URL and AGENT_TOKEN must be set.

Example:
  AGENT_URL=https://wonderstruck.sales-agent.scope3.com \\
  AGENT_TOKEN=<bearer> ./scripts/storyboard-check.sh
EOF
    exit 2
fi

# Strip trailing slash and any /mcp suffix; we'll add per-transport suffixes
# below so MCP and A2A both get the right URL shape.
AGENT_BASE="${AGENT_URL%/}"
AGENT_BASE="${AGENT_BASE%/mcp}"

for cmd in npx jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "✗ $cmd is required" >&2
        exit 2
    fi
done

EXTRA_FLAGS=()
if [[ "$NO_SANDBOX" == "1" ]]; then
    EXTRA_FLAGS+=(--no-sandbox)
fi
if [[ "$ALLOW_HTTP" == "1" ]]; then
    EXTRA_FLAGS+=(--allow-http)
fi

# ─── Per-transport runner ────────────────────────────────────────────────────

# Run the storyboard against one transport and print a one-line summary plus
# a bulleted list of failing steps. Writes raw JSON to /tmp/storyboard-<proto>.json.
# Sets the global RC_PROTO_<protocol> to 0 (all passed) or 1 (any failure).
run_one() {
    local protocol="$1"
    local url="$2"
    local out="/tmp/storyboard-${protocol}.json"
    local err="/tmp/storyboard-${protocol}.err"

    echo "── ${protocol^^} ──"
    echo "→ ${url}"

    # Capture both streams; the JSON summary lives on stderr (the always-on
    # "STORYBOARD-FAIL" output the SDK promises), JSON output on stdout when
    # --format json is set.
    npx -y @adcp/sdk storyboard run "$url" "$STORYBOARD" \
        --auth "$AGENT_TOKEN" \
        --protocol "$protocol" \
        --format json \
        --timeout "$TIMEOUT" \
        "${EXTRA_FLAGS[@]}" \
        >"$out" 2>"$err"
    local exit_code=$?

    # Pull the summary block from stderr — present whether or not the run
    # passed. Falls back to "no summary" when the SDK errored before running.
    local summary
    summary=$(awk '/^──── Storyboard Summary ────/,/^STORYBOARD-/' "$err" | head -20)
    if [[ -z "$summary" ]]; then
        echo "  (no summary — SDK aborted before running; see $err)"
        echo "  $(tail -3 "$err" | head -1)"
        eval "RC_${protocol}=1"
        return
    fi

    # Steps line: "Steps:     0 passed, 11 failed, 48 skipped"
    grep -E "^Steps:" <<<"$summary" | sed 's/^/  /'
    grep -E "^Duration:" <<<"$summary" | sed 's/^/  /'

    # Failing steps (one per line under STORYBOARD-FAIL block)
    local failing
    failing=$(awk '/^STORYBOARD-FAIL/,/^$/' "$err" | grep -E '^\s+- ' | head -50)
    if [[ -n "$failing" ]]; then
        echo "  Failing steps:"
        # Truncate each line at 200 chars so the summary stays readable; full
        # output remains in the .json/.err files.
        echo "$failing" | sed 's/^/  /' | cut -c1-200
    fi

    # passed=0 in summary == failure for our purposes
    if grep -qE "^Steps:.*0 passed" <<<"$summary" || grep -qE "STORYBOARD-FAIL" "$err"; then
        eval "RC_${protocol}=1"
    else
        # Conservative: only mark pass if no STORYBOARD-FAIL line at all
        if grep -qE "STORYBOARD-FAIL" "$err"; then
            eval "RC_${protocol}=1"
        else
            eval "RC_${protocol}=0"
        fi
    fi

    echo
    return $exit_code  # report SDK exit code per transport
}

# ─── Main ────────────────────────────────────────────────────────────────────

echo "Storyboard: $STORYBOARD"
echo "Sandbox:    $([[ $NO_SANDBOX == 1 ]] && echo off || echo on)"
echo

# MCP gets the /mcp path; A2A speaks at the host root per AdCP convention.
RC_mcp=0
RC_a2a=0
run_one mcp "${AGENT_BASE}/mcp" || true
run_one a2a "${AGENT_BASE}/" || true

echo "── Result ──"
echo "MCP: $([[ $RC_mcp == 0 ]] && echo PASS || echo FAIL)"
echo "A2A: $([[ $RC_a2a == 0 ]] && echo PASS || echo FAIL)"

if [[ $RC_mcp != 0 || $RC_a2a != 0 ]]; then
    exit 1
fi
