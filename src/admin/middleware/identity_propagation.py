"""Reader for the embedded-mode identity propagation contract.

Sprint 1 ships only the *reader* — the request-scoping middleware that maps
``X-Identity-Org-Id`` to a tenant lands in sprint 4 along with the rest of
the UI hardening. The reader is exposed now so audit-log code that wants to
attach external-identity columns has a single canonical extraction point.

See [parent design § 2](../../../docs/design/embedded-mode.md#2-authentication-identity-propagation-from-the-platform-edge)
for the contract:

    X-Identity-Email      string, required
    X-Identity-Org-Id     string, required
    X-Identity-Role       enum: admin | member | viewer, required
    X-Identity-Source     string, required
    X-Identity-User-Id    string, optional
    X-Identity-Signature  string, optional (only present when signed mode is on)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PropagatedRole = Literal["admin", "member", "viewer"]

REQUIRED_HEADERS = ("X-Identity-Email", "X-Identity-Org-Id", "X-Identity-Role", "X-Identity-Source")


@dataclass(frozen=True)
class PropagatedIdentity:
    """Trusted identity forwarded from the upstream platform's edge."""

    email: str
    org_id: str
    role: PropagatedRole
    source: str
    user_id: str | None = None
    signature: str | None = None


class InvalidPropagatedIdentity(ValueError):
    """Headers were present but malformed (e.g. unknown role)."""


def _headers_view(request: Any):
    """Return a mapping-like object for the request's headers.

    Accepts a Flask request object (``request.headers``), a raw mapping, or
    anything with a ``.headers`` attribute. Keeping this decoupled from Flask
    makes the reader easy to unit test.
    """
    if hasattr(request, "headers"):
        return request.headers
    return request


def read_identity_from_request(request: Any) -> PropagatedIdentity | None:
    """Return :class:`PropagatedIdentity` if the headers are present, else None.

    The caller decides whether absence is allowed. Sprint 1 callers (audit log
    enrichment) treat absence as "no upstream identity" and proceed; sprint 4
    middleware will fail closed for embedded-mode UI requests.

    Raises:
        InvalidPropagatedIdentity: if the required headers are present but
            ``X-Identity-Role`` is not one of ``admin | member | viewer``.
    """
    headers = _headers_view(request)
    email = headers.get("X-Identity-Email") if hasattr(headers, "get") else None
    if not email:
        return None

    # The contract specifies these three are required when Email is present.
    org_id = headers.get("X-Identity-Org-Id")
    role = headers.get("X-Identity-Role")
    source = headers.get("X-Identity-Source")
    if not (org_id and role and source):
        raise InvalidPropagatedIdentity(
            "X-Identity-Email present but X-Identity-Org-Id / X-Identity-Role / X-Identity-Source missing"
        )

    if role not in ("admin", "member", "viewer"):
        raise InvalidPropagatedIdentity(f"X-Identity-Role must be admin|member|viewer (got {role!r})")

    return PropagatedIdentity(
        email=email,
        org_id=org_id,
        role=role,
        source=source,
        user_id=headers.get("X-Identity-User-Id"),
        signature=headers.get("X-Identity-Signature"),
    )
