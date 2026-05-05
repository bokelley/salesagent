#!/usr/bin/env bash
# Runs ON THE VM. Assumes source has already been rsynced to $APP_DIR by deploy-app.sh.
# Installs Docker if needed, fetches DATABASE_URL from Secret Manager, builds and
# starts the app via docker compose.
#
# Idempotent: safe to re-run.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/sales-agent}"
DB_SECRET="${DB_SECRET:-sales-agent-database-url}"

META="http://metadata.google.internal/computeMetadata/v1"
mdget() { curl -sf -H "Metadata-Flavor: Google" "$META/$1"; }

# --- Install Docker if missing ------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# --- Configure Docker DNS -----------------------------------------------------
# WG's DNS=fdaa::3 became the system resolver, which only knows Fly hosts.
# Docker builds run in bridge-network containers that inherit this and can't
# reach deb.debian.org. Pin Docker's bridge-network DNS to public resolvers.
# Runtime containers use network_mode:host so they bypass this and keep the
# full host resolver chain (including fdaa::3 for *.flympg.net).
install -d /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "dns": ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
}
EOF
systemctl restart docker

# --- Drop the GCP compose override into the repo dir -------------------------
cp /opt/gcp-deploy/docker-compose.gcp.yml "$APP_DIR/docker-compose.gcp.yml"

# --- Pull DATABASE_URL from Secret Manager -----------------------------------
PROJECT=$(mdget "project/project-id")
TOKEN=$(mdget "instance/service-accounts/default/token" | jq -r .access_token)
DB_URL=$(curl -sf -H "Authorization: Bearer $TOKEN" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets/${DB_SECRET}/versions/latest:access" \
  | jq -r '.payload.data' | base64 -d)

cat > "$APP_DIR/.env.gcp" <<EOF
DATABASE_URL=$DB_URL
ADCP_AUTH_TEST_MODE=true
SKIP_NGINX=true
SKIP_CRON=true
ADCP_SALES_PORT=8000
PYTHONUNBUFFERED=1
EOF
chmod 600 "$APP_DIR/.env.gcp"

# --- Build & start ------------------------------------------------------------
cd "$APP_DIR"
docker compose -f docker-compose.gcp.yml --env-file .env.gcp up -d --build --remove-orphans

echo
echo "App is starting. Watch logs with:"
echo "  docker compose -f $APP_DIR/docker-compose.gcp.yml logs -f"
echo
echo "Once healthy, hit http://<VM-public-IP>:8000"
