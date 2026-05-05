#!/usr/bin/env bash
# Verify the GCE VM can reach Fly MPG through the WireGuard tunnel.
# Pass DATABASE_URL as $1 or via env.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT}"
ZONE="${ZONE:-us-east4-a}"
VM_NAME="${VM_NAME:-sales-agent-1}"
MPG_HOST="${MPG_HOST:-direct.1zvn90k54610kpew.flympg.net}"
DB_URL="${1:-${DATABASE_URL:-}}"

run() { gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT" --command="$1"; }

echo "--- WG status ---"
run "sudo wg show flywg"

echo "--- DNS resolution through Fly ---"
run "getent ahosts $MPG_HOST | head -3"

echo "--- TCP reachability ---"
run "timeout 5 bash -c '</dev/tcp/$MPG_HOST/5432' && echo 'tcp 5432 open' || echo 'tcp 5432 BLOCKED'"

if [ -n "$DB_URL" ]; then
  echo "--- Postgres SELECT 1 ---"
  # base64 the URL to bypass shell quoting through gcloud ssh --command.
  # Decode happens on the remote inside command substitution so the URL never appears in argv.
  B64=$(printf '%s' "$DB_URL" | base64 | tr -d '\n')
  run "psql \"\$(echo $B64 | base64 -d)\" -c 'SELECT 1 AS ok, current_database(), version()'"
else
  echo "(skip psql — pass DATABASE_URL as \$1 or env to run it)"
fi
