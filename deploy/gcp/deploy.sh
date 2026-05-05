#!/usr/bin/env bash
# Provision a GCE VM that tunnels to Fly via WireGuard, with WG conf in Secret Manager.
# Run from this directory. Requires: gcloud auth login + a project + ./flywg.conf next to this script... or override WG_CONF.
set -euo pipefail

# --- Config (override via env) -------------------------------------------------
PROJECT="${PROJECT:?set PROJECT=your-gcp-project}"
REGION="${REGION:-us-east4}"          # closest GCP region to Fly iad — keeps DB latency ~1ms
ZONE="${ZONE:-us-east4-a}"
VM_NAME="${VM_NAME:-sales-agent-1}"
MACHINE_TYPE="${MACHINE_TYPE:-e2-small}"
WG_CONF="${WG_CONF:-../../flywg.conf}"
SECRET_NAME="${SECRET_NAME:-sales-agent-flywg}"
SA_NAME="${SA_NAME:-sales-agent-vm}"
MPG_HOST="${MPG_HOST:-direct.1zvn90k54610kpew.flympg.net}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WG_CONF_ABS="$(cd "$(dirname "$WG_CONF")" && pwd)/$(basename "$WG_CONF")"

[ -f "$WG_CONF_ABS" ] || { echo "ERROR: WG conf not found at $WG_CONF_ABS"; exit 1; }

gcloud config set project "$PROJECT"

# --- APIs ----------------------------------------------------------------------
gcloud services enable secretmanager.googleapis.com compute.googleapis.com iam.googleapis.com

# --- Secret: WG conf -----------------------------------------------------------
if ! gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET_NAME" --replication-policy=automatic
fi
gcloud secrets versions add "$SECRET_NAME" --data-file="$WG_CONF_ABS"

# --- Service account for the VM (least-priv: only read this one secret) -------
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --display-name="Sales Agent VM"
fi

# IAM propagation can lag SA creation by 5-30s; binding errors with "does not exist" until then.
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="roles/secretmanager.secretAccessor" >/dev/null 2>&1; then
    echo "IAM binding succeeded (attempt $attempt)"
    break
  fi
  if [ "$attempt" = "10" ]; then
    echo "ERROR: IAM binding failed after 10 attempts" >&2
    exit 1
  fi
  echo "IAM not propagated yet, retrying in 5s (attempt $attempt/10)..."
  sleep 5
done

# --- VM ------------------------------------------------------------------------
# Replace if it already exists — startup script is the source of truth.
if gcloud compute instances describe "$VM_NAME" --zone="$ZONE" >/dev/null 2>&1; then
  echo "VM $VM_NAME exists; deleting and recreating to apply latest startup script."
  gcloud compute instances delete "$VM_NAME" --zone="$ZONE" --quiet
fi

gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --service-account="$SA_EMAIL" \
  --scopes=cloud-platform \
  --metadata="wg-secret-name=${SECRET_NAME},mpg-host=${MPG_HOST}" \
  --metadata-from-file="startup-script=${SCRIPT_DIR}/startup-script.sh" \
  --tags=sales-agent

cat <<EOF

VM created. Watch the WireGuard bring-up:
  gcloud compute instances tail-serial-port-output $VM_NAME --zone=$ZONE

When boot finishes, verify the tunnel and DB reachability:
  ./smoke-test.sh

To SSH:
  gcloud compute ssh $VM_NAME --zone=$ZONE
EOF
