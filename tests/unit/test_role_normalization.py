"""Role normalization + RBAC gate primitives.

Sprint 4 / role enforcement infrastructure. Tests the building blocks
in ``src/admin/utils/helpers.py``:

- ``_normalize_role(raw)`` — maps stored/header values to the canonical
  ``admin | member | viewer`` enum. Legacy ``manager`` rolls into
  ``member``; unknown values clamp to ``viewer`` (least privilege per
  the embedded-mode identity contract).
- ``_role_permits(actual, allowed)`` — membership check on the
  normalized role against an allow-list.

End-to-end behavior of ``_maybe_block_role_gate`` (which uses these
primitives plus ``request.method`` and ``g.user``) is exercised by the
integration tests in ``tests/integration/test_role_enforcement.py``.
"""

from __future__ import annotations

import pytest

from src.admin.utils.helpers import ROLES, _normalize_role, _role_permits

pytestmark = [pytest.mark.admin, pytest.mark.auth]


class TestRoleEnum:
    def test_canonical_enum_is_three_values(self):
        assert ROLES == ("admin", "member", "viewer")


# (raw input, normalized output, why)
NORMALIZE_CASES: list[tuple[str | None, str, str]] = [
    # canonical values pass through
    ("admin", "admin", "canonical admin"),
    ("member", "member", "canonical member"),
    ("viewer", "viewer", "canonical viewer"),
    # legacy ``manager`` rolls in
    ("manager", "member", "manager → member migration mapping"),
    # unknown / missing → viewer (least privilege per contract)
    (None, "viewer", "None / missing → viewer"),
    ("", "viewer", "empty → viewer"),
    ("superuser", "viewer", "unknown → viewer"),
    ("ADMIN", "viewer", "case-sensitive: uppercase ≠ admin"),
    ("Admin", "viewer", "case-sensitive: title-case ≠ admin"),
]


@pytest.mark.parametrize("raw,expected,reason", NORMALIZE_CASES, ids=[c[2] for c in NORMALIZE_CASES])
def test_normalize_role(raw, expected, reason):
    assert _normalize_role(raw) == expected, f"{reason}: _normalize_role({raw!r})"


# (actual role, allowed list, expected verdict, label)
PERMIT_CASES: list[tuple[str, tuple[str, ...] | None, bool, str]] = [
    # admin allowed everywhere
    ("admin", ("admin",), True, "admin into admin-only"),
    ("admin", ("admin", "member"), True, "admin into admin+member"),
    ("admin", ("admin", "member", "viewer"), True, "admin into all"),
    # member granted operational, denied config
    ("member", ("admin", "member"), True, "member into admin+member"),
    ("member", ("admin", "member", "viewer"), True, "member into all"),
    ("member", ("admin",), False, "member denied admin-only"),
    # viewer read-only
    ("viewer", ("admin", "member", "viewer"), True, "viewer into all"),
    ("viewer", ("admin", "member"), False, "viewer denied operational write"),
    ("viewer", ("admin",), False, "viewer denied admin-only"),
    # None allowed list = no gate
    ("admin", None, True, "no policy set → pass"),
    ("viewer", None, True, "no policy set → pass even for viewer"),
]


@pytest.mark.parametrize(
    "actual,allowed,expected,label",
    PERMIT_CASES,
    ids=[c[3] for c in PERMIT_CASES],
)
def test_role_permits(actual, allowed, expected, label):
    assert _role_permits(actual, allowed) is expected, label
