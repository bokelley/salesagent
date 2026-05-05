#!/usr/bin/env bash
# Sync local working tree to the VM (no push required), upload DATABASE_URL to
# Secret Manager, build and start the app. Run from this directory.
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT=your-gcp-project}"
ZONE="${ZONE:-us-east4-a}"
VM_NAME="${VM_NAME:-sales-agent-1}"
DB_SECRET="${DB_SECRET:-sales-agent-database-url}"
SA_EMAIL="${SA_EMAIL:-sales-agent-vm@${PROJECT}.iam.gserviceaccount.com}"
DB_URL="${DATABASE_URL:?set DATABASE_URL=postgresql://...flympg.net/...}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Repo root = two levels up from .context/gcp-deploy/
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

gcloud config set project "$PROJECT"

# --- Secret: DATABASE_URL -----------------------------------------------------
if ! gcloud secrets describe "$DB_SECRET" >/dev/null 2>&1; then
  gcloud secrets create "$DB_SECRET" --replication-policy=automatic
fi
printf '%s' "$DB_URL" | gcloud secrets versions add "$DB_SECRET" --data-file=-

# Grant the VM's SA access (idempotent — succeeds if already bound)
for attempt in 1 2 3 4 5; do
  if gcloud secrets add-iam-policy-binding "$DB_SECRET" \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="roles/secretmanager.secretAccessor" >/dev/null 2>&1; then
    break
  fi
  echo "IAM binding retry $attempt/5..."
  sleep 5
done

# --- Open firewall on port 8000 ----------------------------------------------
# WARNING: opens to the world. Lock to your IP for production:
#   --source-ranges=$(curl -sf https://api.ipify.org)/32
if ! gcloud compute firewall-rules describe allow-sales-agent-8000 >/dev/null 2>&1; then
  gcloud compute firewall-rules create allow-sales-agent-8000 \
    --direction=INGRESS --action=ALLOW --rules=tcp:8000 \
    --source-ranges=0.0.0.0/0 --target-tags=sales-agent
fi

# --- Sync local working tree to the VM ---------------------------------------
# tar-pipe over gcloud ssh: handles excludes natively, captures uncommitted changes,
# avoids needing to push the branch to a git remote.
echo "Syncing $REPO_ROOT to $VM_NAME:/opt/sales-agent ..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command="\
  sudo install -d -o root -g root -m 0755 /opt/sales-agent /opt/gcp-deploy"

# macOS BSD tar embeds Apple xattrs (com.apple.provenance, etc.) that GNU tar
# on Linux warns about. Strip them at the source; suppress survivors on receive.
TAR_OPTS=()
if tar --no-mac-metadata --version >/dev/null 2>&1; then
  TAR_OPTS+=(--no-mac-metadata)
fi
COPYFILE_DISABLE=1 tar -C "$REPO_ROOT" \
    "${TAR_OPTS[@]}" \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.mypy_cache' \
    --exclude='.ruff_cache' \
    --exclude='htmlcov' \
    --exclude='test-results' \
    --exclude='audit_logs' \
    --exclude='.conductor' \
    --exclude='.context' \
    --exclude='coverage.json' \
    --exclude='.coverage*' \
    --exclude='._*' \
    --exclude='.DS_Store' \
    -czf - . \
  | gcloud compute ssh "$VM_NAME" --zone="$ZONE" \
      --command="sudo tar -C /opt/sales-agent --warning=no-unknown-keyword -xzf -"

# --- Copy compose file + install script onto the VM --------------------------
gcloud compute scp \
  "$SCRIPT_DIR/docker-compose.gcp.yml" \
  "$SCRIPT_DIR/install-app.sh" \
  "$VM_NAME":/tmp/ --zone="$ZONE"

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command="\
  sudo mv /tmp/docker-compose.gcp.yml /tmp/install-app.sh /opt/gcp-deploy/ && \
  sudo chmod +x /opt/gcp-deploy/install-app.sh"

# --- Run the install on the VM ------------------------------------------------
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --command="\
  sudo DB_SECRET='$DB_SECRET' /opt/gcp-deploy/install-app.sh"

# --- Show external IP & next steps -------------------------------------------
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
  --format='value(networkInterfaces[0].accessConfigs[0].natIP)')

cat <<EOF

App deployed. External IP: $EXTERNAL_IP
  Admin UI:  http://$EXTERNAL_IP:8000/
  Health:    http://$EXTERNAL_IP:8000/health
  MCP:       http://$EXTERNAL_IP:8000/mcp/
  A2A:       http://$EXTERNAL_IP:8000/a2a

Tail logs:
  gcloud compute ssh $VM_NAME --zone=$ZONE -- \\
    'cd /opt/sales-agent && sudo docker compose -f docker-compose.gcp.yml logs -f'

Test login: click "Log in to Dashboard" — password is test123 (test mode is on)
EOF
