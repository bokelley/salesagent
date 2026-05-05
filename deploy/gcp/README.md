# GCP Deployment

Reference deployment for running the salesagent on a single GCE VM in `us-east4-a`, connecting to a managed Postgres instance on Fly.io (`hosted-sales-agent` cluster in `iad`) over a WireGuard tunnel.

This is the deployment shape used to validate the `embedded-mode` work against production-shape data without touching prod. It is **not** a production-ready setup — see [What's missing for prod](#whats-missing-for-prod).

---

## Architecture

```
Internet (port 8000)              ◄─── DEFAULT-OPEN, lock down before walking away
       │
       ▼
GCE VM (us-east4-a, e2-small, 30GB pd-standard)
 │
 ├── Docker daemon
 │     └─ adcp-server container (network_mode: host)
 │           bind: 0.0.0.0:8000
 │           serves: /, /admin, /mcp/, /a2a, /health
 │
 ├── WireGuard interface (wg-quick@flywg)
 │     conf pulled at boot from Secret Manager
 │     resolves *.flympg.net through fdaa::3 (Fly DNS)
 │
 └── /etc/resolv.conf → fdaa:66:eec0::3 (Fly's recursive resolver)
       │
       ▼
   Fly MPG cluster 1zvn90k54610kpew (iad)
   ├── hosted-sales-agent     ← prod data; do not migrate from this deploy
   ├── fly-db                 ← test sandbox (clone target)
   └── (other tenant DBs)
```

**Key choices:**

- **`us-east4-a` (Ashburn)** ↔ Fly `iad` are in the same NoVA metro. RTT to MPG over the tunnel is ~1–2ms. Don't use `us-central1` — adds ~25ms per query.
- **`network_mode: host`** for the app container. Lets the container reuse the VM's WG route to `fdaa::/16` without bridge-network IPv6 plumbing.
- **`/etc/resolv.conf` bind-mounted into the container.** Docker's `daemon.json dns` setting (which we set to `8.8.8.8` for builds) overrides container resolv.conf even when `network_mode: host`. The bind mount restores the host's actual resolver chain, including `fdaa::3` for `*.flympg.net`.
- **Secret Manager for WG conf and DB URL.** VM has a least-priv service account that can read only those two secrets — the WG private key never sits in instance metadata.

---

## Files

| | |
|---|---|
| `deploy.sh` | One-time VM provisioning. Creates Secret Manager secret for the WG conf, least-priv service account, GCE VM with startup script. |
| `startup-script.sh` | Runs on every VM boot. Installs WG tools, pulls conf from Secret Manager, brings up the tunnel, smoke-tests connectivity. |
| `deploy-app.sh` | Ships the local working tree to the VM (no git push needed), pushes `DATABASE_URL` to Secret Manager, runs `install-app.sh` on the VM. |
| `install-app.sh` | Runs on the VM. Installs Docker if missing, configures Docker daemon DNS, fetches `DATABASE_URL` from Secret Manager, builds and starts the compose stack. |
| `docker-compose.gcp.yml` | Two services: `db-init` (one-shot alembic) and `adcp-server` (FastAPI on `:8000`). No nginx — the FastAPI app serves all routes itself. |
| `smoke-test.sh` | Verifies WG handshake, DNS resolution, TCP reachability to MPG, and (with a `DATABASE_URL` arg) `SELECT 1`. |
| `mcp-demo.py` | Connects to a deployed instance via fastmcp client and lists tools / calls `get_adcp_capabilities`. Uses Python directly because `uvx adcp` strips trailing slashes (see [adcp-client-python upstream issue](#upstream-bug)). |

---

## Prerequisites

1. **WireGuard config** — generate once on Fly:
   ```bash
   flyctl wireguard create <org> iad gcp-sales-agent
   # writes <peer-name>.conf — save as flywg.conf in repo root (gitignored)
   ```
2. **GCP project** with billing enabled and `gcloud` authenticated.
3. **Repo root contains `flywg.conf`** (untracked, gitignored).

---

## Deploy from scratch

```bash
cd deploy/gcp

# 1. Provision the VM (one-time)
PROJECT=your-gcp-project ./deploy.sh

# 2. Verify the WG tunnel works
./smoke-test.sh

# 3. Deploy the app
DATABASE_URL='postgresql://fly-user:<pw>@direct.<cluster>.flympg.net:5432/<dbname>?sslmode=require' \
PROJECT=your-gcp-project \
./deploy-app.sh

# 4. Hit it
curl http://<external-ip>:8000/health
open http://<external-ip>:8000/

# 5. LOCK THE FIREWALL (open by default — change before walking away)
gcloud compute firewall-rules update allow-sales-agent-8000 \
  --source-ranges=$(curl -sf https://api.ipify.org)/32
```

---

## Test against production-shape data without touching prod

The MPG cluster has both `hosted-sales-agent` (live) and `fly-db` (free scratch space). Clone the live DB into `fly-db`, point the test deploy at the clone:

```bash
gcloud compute ssh sales-agent-1 --zone=us-east4-a --command='
  set -e
  DB_URL=$(sudo cat /opt/sales-agent/.env.gcp | grep ^DATABASE_URL | cut -d= -f2-)
  SOURCE_URL="$DB_URL"  # already pointed at hosted-sales-agent
  TARGET_URL=$(echo "$DB_URL" | sed "s|/hosted-sales-agent|/fly-db|")

  # Drop everything in target first (CASCADE handles FKs; fly-user owns its tables but not the public schema)
  sudo docker run --rm --network=host -v /etc/resolv.conf:/etc/resolv.conf:ro postgres:16-alpine \
    psql "$TARGET_URL" -c "
      DO \$\$ DECLARE r RECORD;
      BEGIN
        FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname='"'"'public'"'"') LOOP
          EXECUTE '"'"'DROP TABLE IF EXISTS public.'"'"' || quote_ident(r.tablename) || '"'"' CASCADE'"'"';
        END LOOP;
      END \$\$;"

  # Dump → restore (only public schema — pgbouncer schema is shared and would conflict)
  sudo docker run --rm --network=host -v /etc/resolv.conf:/etc/resolv.conf:ro -v /tmp:/tmp postgres:16-alpine \
    sh -c "pg_dump --no-owner --no-acl --format=custom -f /tmp/dump.pgc \"$SOURCE_URL\" \
           && pg_restore --no-owner --no-acl --no-comments --schema=public --dbname=\"$TARGET_URL\" /tmp/dump.pgc"
'

# Update Secret Manager to point at fly-db
gcloud secrets versions access latest --secret=sales-agent-database-url --project=$PROJECT \
  | sed 's|/hosted-sales-agent|/fly-db|' \
  | gcloud secrets versions add sales-agent-database-url --data-file=- --project=$PROJECT

# Re-add db-init to docker-compose.gcp.yml (test branch's migrations apply to clone), redeploy
DATABASE_URL=$(gcloud secrets versions access latest --secret=sales-agent-database-url --project=$PROJECT) \
PROJECT=your-gcp-project \
./deploy-app.sh
```

Result: app runs against a snapshot of real prod data. Migrations on the test branch apply to the clone, never to prod. To refresh: re-run the dump/restore.

---

## Gotchas (and why)

These all bit us during initial bring-up. Worth knowing before the next deploy.

### 1. `flyctl mpg` doesn't expose public IPs on `starter` plan

**Symptom:** `dig AAAA direct.<cluster>.flympg.net` returns nothing.
**Reason:** managed product — you don't have access to the underlying Fly app to run `flyctl ips allocate-v6` against. Hence WireGuard from a GCE VM is the path; not laziness.

### 2. Docker daemon DNS conflicts with WireGuard DNS

**Symptom:** `apt-get update` inside Docker build container fails with `Temporary failure resolving 'deb.debian.org'`.
**Reason:** WireGuard registered `fdaa::3` as the host resolver. Docker's bridge-network containers inherit it. `fdaa::3` only knows Fly hosts.
**Fix:** `daemon.json` pins bridge-network DNS to `8.8.8.8`. Host-network containers (`adcp-server`) bypass that and bind-mount the host's resolv.conf to keep `fdaa::3` for `*.flympg.net`.

### 3. macOS BSD tar embeds AppleDouble metadata

**Symptom:** `db-init` fails with `source code string cannot contain null bytes` at alembic startup.
**Reason:** macOS file system creates `._filename` files alongside originals. `tar` includes them; alembic's `*.py` glob picks up `._001_link_to_initial.py`; Python tries to import the binary blob.
**Fix:** `--exclude='._*'` in the tar pipe in `deploy-app.sh`. `COPYFILE_DISABLE=1` and `--no-mac-metadata` only prevent *new* AppleDouble files during tar — they don't help if the source dir already has them on disk.

### 4. nginx (initially included) was just a pass-through

The local dev compose includes nginx routing `/admin`, `/mcp`, `/a2a` to a single FastAPI service. FastAPI mounts those sub-apps internally — nginx adds nothing for an HTTP-only test deploy. Removed.
**When to add it back:** TLS termination, rate limiting, static caching. For prod you'd more likely use a GCP HTTP(S) Load Balancer instead.

### 5. WireGuard private key never lives in instance metadata

`startup-script.sh` fetches the WG conf from Secret Manager via the VM's service-account token at boot. The conf is written to `/etc/wireguard/flywg.conf` with mode 0600 and never appears in `gcloud compute instances describe` output.

### 6. Port allocation: VM boot disk

Default GCE boot disk is 10GB. After Docker + Python image base + venv layer + build cache, you'll OOM-disk during build. `deploy.sh` provisions 30GB to leave headroom.

### 7. CREATE DATABASE TEMPLATE doesn't work on MPG `starter`

`fly-user` is `schema_admin` but lacks the cluster-level `CREATEDB` privilege. The clone-via-template path fails with "permission denied to create database." Use `pg_dump | pg_restore` into the existing `fly-db` instead (you own the tables you create there).

### 8. `pg_restore --schema=public` is mandatory

The MPG cluster has a shared `pgbouncer` schema. A `pg_dump` of `hosted-sales-agent` includes `CREATE SCHEMA pgbouncer` as a side effect, which fails when restored into `fly-db` (the schema is shared). `--schema=public` restricts the dump to just app data.

---

## Upstream bug

`uvx adcp` (the AdCP Python CLI) cannot connect to FastMCP-mounted endpoints because its Pydantic validator strips trailing slashes from `agent_uri`. `mcp-demo.py` works around this with the Python `fastmcp.client` directly. Issue draft for `adcontextprotocol/adcp-client-python` is in the workspace context dir.

---

## What's missing for prod

This deploy is a single-VM test target. Before serving real traffic:

- [ ] **Lock the firewall.** The default `0.0.0.0/0` rule is for first-deploy convenience. For embedded mode, this should be Scope3-egress-IPs only, OR replaced with VPC peering / Private Service Connect to Scope3's GCP project.
- [ ] **Real auth.** `ADCP_AUTH_TEST_MODE=true` in `.env.gcp`. Set to `false` and configure `OAUTH_*` env vars when you want the super-admin backdoor; configure `X-Identity-Source` header trust for the host product's reverse-proxied UI.
- [ ] **TLS.** Plain HTTP today. Either Caddy on the VM with auto-Let's-Encrypt, or GCP Internal HTTPS Load Balancer.
- [ ] **HA on the WG tunnel.** Single peer is a SPOF. Stand up a second VM in `us-east4-b` with its own WG peer and load-balance.
- [ ] **PgBouncer.** MPG `starter` has tight connection caps. Drop pgbouncer in front before adding a second app instance.
- [ ] **Migration discipline.** `db-init` running alembic on every deploy is fine for test clones; for prod you migrate deliberately, separately. The current `docker-compose.gcp.yml` includes it for the test-clone workflow — adjust before pointing at prod.

---

## Tear down

```bash
# Stop the VM (preserves disk + image + WG config — easy resume tomorrow)
gcloud compute instances stop sales-agent-1 --zone=us-east4-a --project=$PROJECT

# Or fully delete (and re-provision via deploy.sh next time)
gcloud compute instances delete sales-agent-1 --zone=us-east4-a --project=$PROJECT --quiet
gcloud compute disks delete sales-agent-1 --zone=us-east4-a --project=$PROJECT --quiet 2>/dev/null || true

# Drop the firewall rule (cleanest — VM stays up but unreachable from internet)
gcloud compute firewall-rules delete allow-sales-agent-8000 --project=$PROJECT --quiet

# Secrets are cheap to keep ($0 if accessed <10k/mo) — leave them for next session.
```

Resume:

```bash
gcloud compute instances start sales-agent-1 --zone=us-east4-a --project=$PROJECT
# WG comes back automatically via wg-quick@flywg systemd unit.
# Re-run firewall create or run deploy-app.sh to redeploy code changes.
```
