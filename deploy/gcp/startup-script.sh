#!/usr/bin/env bash
# GCE startup script: bring up WireGuard tunnel to Fly's 6PN, verify connectivity to MPG.
# Runs on every boot. Idempotent.
set -euo pipefail

exec > >(tee -a /var/log/wg-startup.log) 2>&1
echo "=== wg-startup $(date -u +%FT%TZ) ==="

# --- Read instance metadata ----------------------------------------------------
META="http://metadata.google.internal/computeMetadata/v1"
mdget() { curl -sf -H "Metadata-Flavor: Google" "$META/$1"; }

WG_SECRET=$(mdget "instance/attributes/wg-secret-name")
PROJECT=$(mdget "project/project-id")
MPG_HOST=$(mdget "instance/attributes/mpg-host" || echo "")  # optional smoke-test target

# --- Install packages ----------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y wireguard-tools openresolv ca-certificates jq postgresql-client iputils-ping

# --- Pull WG conf from Secret Manager via metadata-server token ----------------
TOKEN=$(mdget "instance/service-accounts/default/token" | jq -r .access_token)
install -m 0700 -d /etc/wireguard
curl -sf -H "Authorization: Bearer $TOKEN" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets/${WG_SECRET}/versions/latest:access" \
  | jq -r '.payload.data' | base64 -d > /etc/wireguard/flywg.conf
chmod 600 /etc/wireguard/flywg.conf

# --- Bring up tunnel -----------------------------------------------------------
# wg-quick reads DNS=fdaa::3 from the conf and registers it with openresolv,
# so *.flympg.net and *.internal resolve through Fly's DNS while the tunnel is up.
systemctl enable wg-quick@flywg
systemctl restart wg-quick@flywg

# --- Verify --------------------------------------------------------------------
# Handshake takes a few seconds. Retry briefly so boot logs show success/failure clearly.
for i in 1 2 3 4 5; do
  if wg show flywg latest-handshakes | awk '{print $2}' | grep -qv '^0$'; then
    echo "WG handshake established"
    break
  fi
  echo "waiting for handshake ($i/5)..."
  sleep 3
done
wg show flywg

# Optional: resolve and ping MPG host. Non-fatal — boot continues either way.
if [ -n "$MPG_HOST" ]; then
  echo "--- MPG smoke test: $MPG_HOST ---"
  getent ahosts "$MPG_HOST" || echo "DNS lookup failed"
  ping6 -c 2 -W 3 "$MPG_HOST" || echo "ping6 failed (expected if ICMP blocked; tcp may still work)"
fi

echo "=== wg-startup done $(date -u +%FT%TZ) ==="
