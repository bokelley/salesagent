"""Unit tests for SigningVerifyMiddleware path/header filters + state plumbing.

PR 2B of [signing-non-embedded](../../../docs/design/signing-non-embedded.md).

End-to-end verification (signed accept / bad-sig reject / replay across
workers) is integration-only — needs a running brand.json receiver and
PgReplayStore. Those land in PR 2B's integration suite. This module covers
the deterministic pieces:

* Path-prefix filter (only buyer-protocol paths)
* Cheap header pre-check
* Verified-state contextvar bridge
* ``ResolvedIdentity`` threading via ``resolve_identity_from_context``
"""

from __future__ import annotations

import asyncio

from src.core.signing import (
    VerifiedRequestState,
    clear_verified_state,
    get_verified_state,
    set_verified_state,
)
from src.core.signing.middleware import (
    _extract_operation,
    _has_signature_headers,
    _is_buyer_protocol_path,
    _read_body,
    _replay_receive,
)


class TestBuyerProtocolPathFilter:
    def test_mcp_paths_match(self):
        assert _is_buyer_protocol_path("/mcp")
        assert _is_buyer_protocol_path("/mcp/")
        assert _is_buyer_protocol_path("/mcp/some/sub")

    def test_a2a_paths_match(self):
        assert _is_buyer_protocol_path("/a2a")
        assert _is_buyer_protocol_path("/a2a/")
        assert _is_buyer_protocol_path("/a2a/messages")

    def test_root_matches(self):
        # AdCP A2A serves at host root.
        assert _is_buyer_protocol_path("/")

    def test_admin_paths_excluded(self):
        assert not _is_buyer_protocol_path("/admin")
        assert not _is_buyer_protocol_path("/admin/dashboard")
        assert not _is_buyer_protocol_path("/health")
        assert not _is_buyer_protocol_path("/static/foo.js")

    def test_well_known_excluded(self):
        # Discovery docs (agent-card, brand.json) MUST stay unsigned —
        # they're how counterparties bootstrap.
        assert not _is_buyer_protocol_path("/.well-known/agent.json")
        assert not _is_buyer_protocol_path("/.well-known/agent-card.json")
        assert not _is_buyer_protocol_path("/.well-known/jwks.json")


class TestSignatureHeaderSniff:
    def test_no_headers(self):
        assert not _has_signature_headers({"headers": []})

    def test_only_signature(self):
        assert not _has_signature_headers({"headers": [(b"signature", b"sig=:x:")]})

    def test_only_input(self):
        assert not _has_signature_headers({"headers": [(b"signature-input", b"sig=()")]})

    def test_both_present(self):
        assert _has_signature_headers({"headers": [(b"signature", b"sig=:x:"), (b"signature-input", b"sig=()")]})

    def test_case_insensitive(self):
        assert _has_signature_headers({"headers": [(b"Signature", b"x"), (b"Signature-Input", b"y")]})

    def test_other_headers_ignored(self):
        assert _has_signature_headers(
            {
                "headers": [
                    (b"host", b"example.com"),
                    (b"signature", b"sig=:x:"),
                    (b"content-type", b"application/json"),
                    (b"signature-input", b"sig=()"),
                ]
            }
        )


class TestVerifiedStateContextvar:
    def setup_method(self):
        clear_verified_state()

    def teardown_method(self):
        clear_verified_state()

    def test_default_is_none(self):
        assert get_verified_state() is None

    def test_roundtrip(self):
        state = VerifiedRequestState(
            principal_id="op_123",
            agent_url="https://buyer.example.com/.well-known/agent.json",
            key_id="kid-2026-q2",
        )
        set_verified_state(state)
        roundtrip = get_verified_state()
        assert roundtrip is not None
        assert roundtrip.principal_id == "op_123"
        assert roundtrip.agent_url == "https://buyer.example.com/.well-known/agent.json"
        assert roundtrip.key_id == "kid-2026-q2"

    def test_clear(self):
        set_verified_state(VerifiedRequestState(principal_id="o", agent_url="u", key_id="k"))
        assert get_verified_state() is not None
        clear_verified_state()
        assert get_verified_state() is None

    def test_frozen(self):
        import dataclasses

        state = VerifiedRequestState(principal_id="o", agent_url="u", key_id="k")
        try:
            state.principal_id = "o2"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("VerifiedRequestState must be frozen")


class TestOperationExtraction:
    """PR 2C: parse the AdCP operation name from the request body so the
    middleware can enforce ``TenantSigningPolicy.required_for``."""

    def test_mcp_jsonrpc_tools_call(self):
        body = b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"create_media_buy","arguments":{}},"id":1}'
        assert _extract_operation("/mcp/", body) == "create_media_buy"

    def test_mcp_other_method_passthrough(self):
        # JSON-RPC method that's not tools/call → fall through to method name.
        body = b'{"jsonrpc":"2.0","method":"initialize","id":1}'
        assert _extract_operation("/mcp/", body) == "initialize"

    def test_a2a_skill_field(self):
        body = b'{"skill":"create_media_buy","payload":{}}'
        assert _extract_operation("/a2a", body) == "create_media_buy"

    def test_a2a_message_type_field(self):
        body = b'{"message_type":"create_media_buy","data":{}}'
        assert _extract_operation("/a2a", body) == "create_media_buy"

    def test_unparseable_body_returns_none(self):
        assert _extract_operation("/mcp/", b"not json") is None

    def test_empty_body_returns_none(self):
        assert _extract_operation("/mcp/", b"") is None

    def test_non_dict_payload_returns_none(self):
        assert _extract_operation("/mcp/", b'["array","not","object"]') is None

    def test_no_recognized_field(self):
        assert _extract_operation("/mcp/", b'{"foo":"bar"}') is None


class TestBodyReplay:
    """The middleware reads the body once for the verifier and replays it to
    downstream handlers. Both halves of that contract have to hold."""

    def test_read_body_buffers_chunks(self):
        chunks = [
            {"type": "http.request", "body": b"hello ", "more_body": True},
            {"type": "http.request", "body": b"world", "more_body": False},
        ]
        idx = 0

        async def receive():
            nonlocal idx
            msg = chunks[idx]
            idx += 1
            return msg

        result = asyncio.run(_read_body(receive))
        assert result == b"hello world"

    def test_read_body_respects_cap(self):
        big = b"x" * 100

        async def receive():
            return {"type": "http.request", "body": big, "more_body": False}

        try:
            asyncio.run(_read_body(receive, max_bytes=10))
        except ValueError as exc:
            assert "exceeded" in str(exc)
            return
        raise AssertionError("expected ValueError on oversized body")

    def test_replay_emits_body_then_disconnect(self):
        replay = _replay_receive(b"payload")
        first = asyncio.run(replay())
        second = asyncio.run(replay())
        third = asyncio.run(replay())
        assert first == {"type": "http.request", "body": b"payload", "more_body": False}
        assert second == {"type": "http.disconnect"}
        assert third == {"type": "http.disconnect"}  # idempotent after close

    def test_replay_serves_empty_body(self):
        replay = _replay_receive(b"")
        first = asyncio.run(replay())
        assert first == {"type": "http.request", "body": b"", "more_body": False}
