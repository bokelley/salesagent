"""Unit tests for the embedded-mode identity propagation reader.

Sprint 1 only ships the reader (header parser); request scoping is sprint 4.
These tests cover:
- All four required headers present → returns PropagatedIdentity
- Headers absent → returns None (caller decides if absence is allowed)
- Required headers partially present → InvalidPropagatedIdentity
- Bad role value → InvalidPropagatedIdentity
"""

from __future__ import annotations

import pytest

from src.admin.middleware.identity_propagation import (
    InvalidPropagatedIdentity,
    PropagatedIdentity,
    read_identity_from_request,
)


def _request(headers: dict[str, str]):
    """Build a minimal request stub the reader can introspect."""

    class _Stub:
        def __init__(self, h: dict[str, str]):
            self.headers = h

    return _Stub(headers)


def test_reader_returns_none_when_email_absent():
    assert read_identity_from_request(_request({})) is None


def test_reader_happy_path_minimum_required():
    req = _request(
        {
            "X-Identity-Email": "alice@scope3.test",
            "X-Identity-Org-Id": "org_42",
            "X-Identity-Role": "admin",
            "X-Identity-Source": "scope3",
        }
    )
    identity = read_identity_from_request(req)
    assert isinstance(identity, PropagatedIdentity)
    assert identity.email == "alice@scope3.test"
    assert identity.role == "admin"
    assert identity.user_id is None
    assert identity.signature is None


def test_reader_passes_optional_headers():
    req = _request(
        {
            "X-Identity-Email": "bob@scope3.test",
            "X-Identity-Org-Id": "org_42",
            "X-Identity-Role": "viewer",
            "X-Identity-Source": "scope3",
            "X-Identity-User-Id": "u_123",
            "X-Identity-Signature": "abc.def",
        }
    )
    identity = read_identity_from_request(req)
    assert identity is not None
    assert identity.user_id == "u_123"
    assert identity.signature == "abc.def"


def test_reader_rejects_partial_required_headers():
    req = _request({"X-Identity-Email": "alice@scope3.test"})
    with pytest.raises(InvalidPropagatedIdentity):
        read_identity_from_request(req)


def test_reader_rejects_bad_role():
    req = _request(
        {
            "X-Identity-Email": "alice@scope3.test",
            "X-Identity-Org-Id": "org_42",
            "X-Identity-Role": "owner",  # not in admin|member|viewer
            "X-Identity-Source": "scope3",
        }
    )
    with pytest.raises(InvalidPropagatedIdentity):
        read_identity_from_request(req)
