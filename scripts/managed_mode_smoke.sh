#!/usr/bin/env bash
# Managed-mode end-to-end smoke for the local core stack.
#
# Provisions a Mock-adapter tenant via the Tenant Management API, hits the
# preview-adapter and status endpoints, and prints what Scope3 needs to wire
# its Storefront integration against. Idempotent on re-run (409 + retry with
# a unique external_org_id).
#
# Usage:
#   docker compose -p core -f docker-compose.core.yml up -d
#   ./scripts/managed_mode_smoke.sh                          # default base URL + key
#   BASE_URL=http://localhost:3091 ./scripts/managed_mode_smoke.sh
#   TENANT_MANAGEMENT_API_KEY=<real-key> ./scripts/managed_mode_smoke.sh

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:3091}"
API_KEY="${TENANT_MANAGEMENT_API_KEY:-dev-tenant-management-key-change-me}"
ORG_ID="${ORG_ID:-smoke-$(date +%s)}"

# `jq` is the only non-stdlib dep — abort early with a clear message if missing.
if ! command -v jq >/dev/null 2>&1; then
    echo "✗ jq is required (brew install jq | apt-get install jq)" >&2
    exit 1
fi

echo "→ Base URL:           $BASE_URL"
echo "→ Management API key: ${API_KEY:0:12}..."
echo "→ external_org_id:    $ORG_ID"
echo

# 1. Health check — proves the management API is reachable + key is accepted.
echo "1/4  GET /api/v1/tenant-management/health"
curl -fsS -H "X-Tenant-Management-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/tenant-management/health" \
    | jq -c .
echo

# 2. Preview the Mock adapter — no DB writes; confirms the endpoint is wired.
echo "2/4  POST /api/v1/tenant-management/tenants/preview-adapter (mock)"
curl -fsS -X POST \
    -H "X-Tenant-Management-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"adapter": {"type": "mock", "dry_run": true}}' \
    "$BASE_URL/api/v1/tenant-management/tenants/preview-adapter" \
    | jq -c .
echo

# 3. Provision a tenant with a Mock adapter.
echo "3/4  POST /api/v1/tenant-management/tenants/provision"
PROVISION_BODY=$(cat <<JSON
{
  "name": "Smoke Test Tenant",
  "external_org_id": "$ORG_ID",
  "external_source": "smoke",
  "contact_email": "smoke@example.com",
  "public_agent_url": "https://interchange.io",
  "adapter": {"type": "mock", "dry_run": true},
  "default_currency": "USD",
  "billing_plan": "standard",
  "initial_principal": {"name": "Smoke Test Advertiser"}
}
JSON
)
PROVISION_RESPONSE=$(curl -fsS -X POST \
    -H "X-Tenant-Management-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d "$PROVISION_BODY" \
    "$BASE_URL/api/v1/tenant-management/tenants/provision")
echo "$PROVISION_RESPONSE" | jq -c '{tenant_id, name, external_org_id, mcp_url, a2a_url, admin_url_path}'
TENANT_ID=$(echo "$PROVISION_RESPONSE" | jq -r '.tenant_id')
echo

# 4. Fetch the consolidated status snapshot.
echo "4/4  GET /api/v1/tenant-management/tenants/$TENANT_ID/status"
curl -fsS -H "X-Tenant-Management-API-Key: $API_KEY" \
    "$BASE_URL/api/v1/tenant-management/tenants/$TENANT_ID/status" \
    | jq -c '{adapter, media_buys, packages, creatives, fetched_at}'
echo

echo "✓ Smoke passed. Tenant id: $TENANT_ID"
echo
echo "Next steps for Storefront integration:"
echo "  • OpenAPI spec:     $BASE_URL/api/v1/tenant-management/docs/openapi.json"
echo "  • Swagger UI:       $BASE_URL/api/v1/tenant-management/docs/swagger/"
echo "  • Static OpenAPI:   docs/api/tenant-management-openapi.{json,yaml} (committed)"
echo "  • Identity contract: docs/integration/managed-mode-identity-contract.md"
