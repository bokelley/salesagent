# Embedded Mode — Identity Propagation Contract

**Version:** v1
**Status:** Stable
**Owner:** Sales Agent
**Audience:** Upstream platforms running the salesagent in embedded mode (e.g., Scope3 Storefront)
**Last updated:** 2026-05-04

This document specifies the contract by which an upstream platform passes authenticated user identity to the salesagent. It is the integration spec; design rationale lives in [`docs/design/embedded-mode.md`](../design/embedded-mode.md).

This contract is stable. Breaking changes will be published as `v2`. Non-breaking additions (new optional headers, new role values) may be added without a version bump.

## Applicability

This contract applies when:
- The salesagent is configured with `MANAGED_INSTANCE=true`.
- Requests reach the salesagent through a reverse proxy from the upstream platform.
- The upstream platform has authenticated the user and forwards their identity to the salesagent.

It does **not** apply to:
- Open instances (`MANAGED_INSTANCE=false`) — those use the salesagent's native Google OAuth flow.
- Tenant Management API calls — those use API key auth (`X-Tenant-Management-API-Key`).
- Buyer-protocol calls (MCP/A2A) — those use a related but separate header-based scoping mechanism (forthcoming with sprint 2).
- Salesagent staff super-admin backdoor — uses Google OAuth on a private network.

## Headers

The upstream platform MUST set the following headers on every proxied request:

| Header | Type | Required | Description |
|---|---|---|---|
| `X-Identity-Email` | string | yes | Authenticated user's email address |
| `X-Identity-Org-Id` | string | yes | Stable identifier for the user's organization in the upstream platform. The salesagent maps this to a tenant via `Tenant.external_org_id`. |
| `X-Identity-Role` | enum | yes | One of `admin`, `member`, `viewer`. See "Role enum" below. |
| `X-Identity-Source` | string | yes | Identifies the upstream platform (e.g., `scope3`, `acme-storefront`). Used for audit logs and multi-platform deployments. |
| `X-Identity-User-Id` | string | no | Stable user ID from the upstream platform. Used for audit; not required for authorization. |
| `X-Identity-Signature` | string | no | Cryptographic signature of the identity. Required only when `IDENTITY_TRUST_MODE=signed` (see "Trust mode"). |

Header values must be ASCII; non-ASCII characters in email/name fields should be percent-encoded by the platform before forwarding.

## Role enum

`X-Identity-Role` must be one of:

| Role | Capabilities |
|---|---|
| `admin` | Full read + write access to publisher-managed surfaces; cannot modify platform-managed surfaces (those are API-only). |
| `member` | Read + write access to operational surfaces (workflows, creatives, media-buy reads); cannot modify configuration. |
| `viewer` | Read-only access. |

Role mapping from upstream-platform-specific roles is the platform's responsibility before the request leaves the platform.

Future roles may be added without a `v2` bump; consumers of `X-Identity-Role` should treat unknown values as `viewer` (least privilege) and log a warning.

## Trust mode

The salesagent's behavior toward identity headers is controlled by the `IDENTITY_TRUST_MODE` environment variable:

| Value | Behavior |
|---|---|
| `network` (default for `MANAGED_INSTANCE=true`) | Headers are trusted as-is. Trust is established by the network: the salesagent is reachable only through the upstream platform's authenticated proxy, so any request that reaches the salesagent has already passed the platform's auth. The `X-Identity-Signature` header, if present, is ignored. |
| `signed` | The salesagent verifies `X-Identity-Signature` against a configured public key (HMAC-SHA256 by default; algorithm configurable). Requests without a valid signature are rejected with 403. Use this mode when the network trust assumption is weaker (e.g., multiple internal services share the salesagent's network without an authenticated proxy in front). |

For deployments where the salesagent is reachable only through the host's authenticated proxy (e.g., the Scope3 reference): `IDENTITY_TRUST_MODE=network`. No signature required.

## Failure modes

| Condition | HTTP | Error code |
|---|---|---|
| `MANAGED_INSTANCE=true` and any required header missing | 403 | `identity_required` |
| `X-Identity-Org-Id` does not match the URL tenant's `external_org_id` | 403 | `identity_org_mismatch` |
| `IDENTITY_TRUST_MODE=signed` and `X-Identity-Signature` missing or invalid | 403 | `identity_signature_invalid` |
| Source IP outside the configured network policy CIDR | 403 | `network_policy_denied` |

The error response body follows the standard `ApiError` shape:

```json
{
  "error": "identity_required",
  "message": "Embedded mode requires X-Identity-* headers; missing X-Identity-Email, X-Identity-Org-Id",
  "details": { "missing_headers": ["X-Identity-Email", "X-Identity-Org-Id"] }
}
```

## Listener hardening (deployment requirement)

Because the contract relies on network trust in the default `network` mode, the salesagent's deployment must:

- Bind the salesagent listener to a private interface only — never `0.0.0.0` on an embedded instance.
- Allow-list the upstream proxy's source IP/range at the salesagent's listener (`BUYER_PROTOCOL_ALLOWED_CIDRS`, `MANAGEMENT_API_ALLOWED_CIDRS`, `ADMIN_UI_ALLOWED_CIDRS` — see deployment docs).
- Reject any request missing the required `X-Identity-*` headers — fail closed.
- Audit-log the headers on every request for post-hoc detection.

These are non-optional. The salesagent will fail to start if `MANAGED_INSTANCE=true` and the CIDR env vars are unset.

## Audit log capture

The salesagent records identity headers in audit log entries for embedded-tenant mutations:

- `audit_logs.external_user_email` ← `X-Identity-Email`
- `audit_logs.external_user_id` ← `X-Identity-User-Id`
- `audit_logs.external_org_id` ← `X-Identity-Org-Id`
- `audit_logs.external_source` ← `X-Identity-Source`

Tenant Management API calls (no identity headers; uses API key) record `external_source = "management_api"`.
Super-admin overrides (Google OAuth bypass) record `external_source = "super_admin_override"`.

## Versioning

This is `v1`. Future versions:

- **Non-breaking** (no version bump): adding new optional headers, adding new values to `X-Identity-Role`.
- **Breaking** (`v2`): changing required headers, changing semantics of existing fields, removing roles.

Breaking changes will be announced with a deprecation window of at least 90 days. The `v1` contract will be supported in parallel during the transition.

## Reference: applying the contract (upstream platform side)

A reverse proxy in front of the salesagent should:

1. Authenticate the user (e.g., via Google OIDC or platform-native auth).
2. Resolve the user's organization and role within the platform.
3. Forward the request to the salesagent with the headers above set.
4. Pass the original `Host` and protocol via `X-Forwarded-*` headers (`X-Forwarded-Host`, `X-Forwarded-Proto`, `X-Forwarded-Prefix`) so the salesagent renders correct absolute URLs under the path-prefix mount.

Example nginx snippet (embedded instance, `network` trust mode):

```nginx
location /storefront/salesagent/ {
    proxy_pass http://salesagent.internal/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /storefront/salesagent;

    # Identity contract — set after upstream auth resolves these
    proxy_set_header X-Identity-Email $auth_user_email;
    proxy_set_header X-Identity-Org-Id $auth_org_id;
    proxy_set_header X-Identity-Role $auth_role;
    proxy_set_header X-Identity-Source "scope3";
    proxy_set_header X-Identity-User-Id $auth_user_id;
}
```
