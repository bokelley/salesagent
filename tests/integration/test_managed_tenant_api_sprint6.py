"""Integration tests for Sprint 6 Tenant Management API outbound webhooks.

Covers the 5 endpoints added in
``docs/design/embedded-mode-sprint-6.md``:

- list / create / get / delete (soft) / test webhook subscriptions

Plus the event publication path: approving a workflow fires
``workflow.decided`` to subscribers and the receiver gets a valid HMAC
signature it can verify with the SDK's
:func:`adcp.signing.webhook_hmac.verify_webhook_hmac`.

Each endpoint exercises happy path + 401 (missing key) + 404 (unknown id).
The signing roundtrip recomputes the HMAC client-side using the plaintext
secret returned at create time.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from adcp.signing.webhook_hmac import LegacyWebhookHmacOptions, verify_webhook_hmac
from flask import Flask

from src.admin.services import webhook_publisher
from src.admin.tenant_management_api import tenant_management_api
from src.core.database.repositories.webhook_subscription import hash_secret
from tests.factories import (
    ContextFactory,
    ObjectWorkflowMappingFactory,
    PrincipalFactory,
    TenantFactory,
    WebhookSubscriptionFactory,
    WorkflowStepFactory,
)
from tests.helpers.managed_tenant_api import bind_factories_to_session, install_management_api_key

pytestmark = [pytest.mark.integration, pytest.mark.requires_db]


API_KEY = "sk-sprint-6-test-key"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def install_api_key(integration_db):
    return install_management_api_key(API_KEY)


@pytest.fixture
def app(integration_db, install_api_key, monkeypatch):
    # Allow http://127.0.0.1 destinations from tests; the URL validator
    # blocks RFC1918 by default.
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_IPS", "true")
    application = Flask(__name__)
    application.config["TESTING"] = True
    application.register_blueprint(tenant_management_api)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth_headers(install_api_key):
    return {"X-Tenant-Management-API-Key": install_api_key}


@pytest.fixture
def bound_factories(integration_db):
    with bind_factories_to_session() as session:
        yield session


@pytest.fixture
def tenant(bound_factories):
    return TenantFactory()


@pytest.fixture
def other_tenant(bound_factories):
    return TenantFactory()


@pytest.fixture(autouse=True)
def reset_webhook_secret_cache():
    """Each test starts with a fresh in-process secret cache."""
    webhook_publisher.reset_secret_cache()
    yield
    webhook_publisher.reset_secret_cache()


# ---------------------------------------------------------------------------
# Mock HTTP receiver
# ---------------------------------------------------------------------------


class _MockReceiver:
    """Capture POSTs the webhook delivery service makes.

    Implements ``httpx.AsyncClient.post`` enough to satisfy
    :func:`webhook_delivery._post_signed`. The next-call status is
    configurable so tests can simulate 200/202 success and 502 failure.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.next_status: int = 200
        self.next_body: bytes = b'{"received": true}'

    async def post(self, url: str, *, content: bytes, headers: dict[str, str]):
        self.calls.append({"url": url, "content": content, "headers": dict(headers)})
        return httpx.Response(
            status_code=self.next_status,
            content=self.next_body,
            request=httpx.Request("POST", url, content=content, headers=headers),
        )

    # The real httpx.AsyncClient is used as ``async with`` in some paths.
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


# ---------------------------------------------------------------------------
# Auth (401)
# ---------------------------------------------------------------------------


class TestAuthRequired:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/v1/tenant-management/tenants/t1/webhooks"),
            ("POST", "/api/v1/tenant-management/tenants/t1/webhooks"),
            ("GET", "/api/v1/tenant-management/tenants/t1/webhooks/wh1"),
            ("DELETE", "/api/v1/tenant-management/tenants/t1/webhooks/wh1"),
            ("POST", "/api/v1/tenant-management/tenants/t1/webhooks/wh1/test"),
        ],
    )
    def test_missing_api_key_returns_401(self, client, method, path):
        response = client.open(
            path,
            method=method,
            json={"url": "https://example.com/x"} if method == "POST" else None,
        )
        assert response.status_code == 401, response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateWebhook:
    def test_404_when_tenant_unknown(self, client, auth_headers):
        response = client.post(
            "/api/v1/tenant-management/tenants/no_such_tenant/webhooks",
            headers=auth_headers,
            json={"url": "https://receiver.example.com/hook"},
        )
        assert response.status_code == 404
        assert response.get_json()["error"] == "tenant_not_found"

    def test_create_returns_secret_once(self, client, auth_headers, tenant):
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={
                "url": "https://receiver.example.com/hook",
                "event_types": ["workflow.decided"],
                "description": "test sub",
            },
        )
        assert response.status_code == 201, response.get_data(as_text=True)
        body = response.get_json()
        assert body["webhook_id"].startswith("wh_")
        assert body["url"] == "https://receiver.example.com/hook"
        assert body["event_types"] == ["workflow.decided"]
        assert body["is_active"] is True
        # Secret is in the create response only.
        assert isinstance(body["secret"], str)
        assert len(body["secret"]) >= 32

    def test_rejects_http_url(self, client, auth_headers, tenant, monkeypatch):
        # Without the dev override flag, http:// URLs are rejected.
        monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_IPS", raising=False)
        monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={"url": "http://receiver.example.com/hook"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"] == "webhook_url_not_https"

    def test_rejects_private_ip(self, client, auth_headers, tenant, monkeypatch):
        monkeypatch.delenv("WEBHOOK_ALLOW_PRIVATE_IPS", raising=False)
        monkeypatch.delenv("ADCP_AUTH_TEST_MODE", raising=False)
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={"url": "https://10.0.0.5/hook"},
        )
        assert response.status_code == 400
        assert response.get_json()["error"] == "webhook_url_blocked"

    def test_empty_event_types_means_all(self, client, auth_headers, tenant):
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={"url": "https://receiver.example.com/hook", "event_types": []},
        )
        assert response.status_code == 201
        assert response.get_json()["event_types"] == []


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


class TestListWebhooks:
    def test_list_omits_secret(self, client, auth_headers, tenant):
        # Create one
        create = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={"url": "https://receiver.example.com/hook"},
        )
        assert create.status_code == 201

        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["count"] == 1
        webhook = body["webhooks"][0]
        assert "secret" not in webhook
        assert "secret_hash" not in webhook
        assert webhook["url"] == "https://receiver.example.com/hook"

    def test_cross_tenant_isolation(self, client, auth_headers, tenant, other_tenant, bound_factories):
        WebhookSubscriptionFactory(tenant=tenant)
        WebhookSubscriptionFactory(tenant=other_tenant)
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.get_json()["count"] == 1


class TestGetWebhook:
    def test_404_unknown(self, client, auth_headers, tenant):
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/no_such",
            headers=auth_headers,
        )
        assert response.status_code == 404
        assert response.get_json()["error"] == "webhook_not_found"

    def test_happy_path(self, client, auth_headers, tenant, bound_factories):
        sub = WebhookSubscriptionFactory(tenant=tenant, event_types=["workflow.created", "workflow.decided"])
        response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/{sub.webhook_id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["webhook_id"] == sub.webhook_id
        assert "secret" not in body
        assert body["event_types"] == ["workflow.created", "workflow.decided"]


# ---------------------------------------------------------------------------
# Delete (soft)
# ---------------------------------------------------------------------------


class TestDeleteWebhook:
    def test_soft_delete_preserves_row(self, client, auth_headers, tenant, integration_db, bound_factories):
        sub = WebhookSubscriptionFactory(tenant=tenant)
        webhook_id = sub.webhook_id

        response = client.delete(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/{webhook_id}",
            headers=auth_headers,
        )
        assert response.status_code == 204

        # Subscription is filtered out of active reads...
        get_response = client.get(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/{webhook_id}",
            headers=auth_headers,
        )
        assert get_response.status_code == 404

        # ...but the row still exists with is_active=False.
        from src.core.database.database_session import get_db_session
        from src.core.database.repositories import WebhookSubscriptionRepository

        with get_db_session() as session:
            repo = WebhookSubscriptionRepository(session, tenant.tenant_id)
            persisted = repo.get_by_id(webhook_id, include_inactive=True)
            assert persisted is not None
            assert persisted.is_active is False

    def test_404_unknown(self, client, auth_headers, tenant):
        response = client.delete(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/no_such",
            headers=auth_headers,
        )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test endpoint
# ---------------------------------------------------------------------------


class TestTestEndpoint:
    def test_404_unknown(self, client, auth_headers, tenant):
        response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/no_such/test",
            headers=auth_headers,
        )
        assert response.status_code == 404

    def test_test_endpoint_signs_correctly(self, client, auth_headers, tenant, monkeypatch):
        # 1. Create webhook (caches plaintext secret in publisher cache).
        create = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={
                "url": "http://127.0.0.1:9999/hook",
                "event_types": ["workflow.decided"],
            },
        )
        assert create.status_code == 201
        plaintext_secret = create.get_json()["secret"]
        webhook_id = create.get_json()["webhook_id"]

        # 2. Patch the delivery layer's HTTP client to capture signatures.
        receiver = _MockReceiver()

        from src.admin.services import webhook_delivery

        async def fake_post_signed(url, secret, payload, extra_headers, *, client=None, timeout=10.0):
            # Re-derive headers + body the same way the real impl does.
            from adcp.webhooks import sign_legacy_webhook

            headers, body = sign_legacy_webhook(secret, payload)
            headers["Content-Type"] = "application/json"
            response = await receiver.post(url, content=body, headers=headers)
            return response.status_code, 5, None

        monkeypatch.setattr(webhook_delivery, "_post_signed", fake_post_signed)

        # 3. Fire test endpoint.
        test_response = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks/{webhook_id}/test",
            headers=auth_headers,
        )
        assert test_response.status_code == 200, test_response.get_data(as_text=True)
        body = test_response.get_json()
        assert body["delivered"] is True
        # One synthetic event per subscribed type.
        assert len(body["results"]) == 1
        assert body["results"][0]["event_type"] == "workflow.decided"
        assert body["results"][0]["delivered"] is True
        assert body["results"][0]["response_status"] == 200

        # 4. Verify the receiver got a signed POST whose signature
        #    verifies against the plaintext secret using the SDK verifier.
        assert len(receiver.calls) == 1
        call = receiver.calls[0]
        verified = verify_webhook_hmac(
            headers=call["headers"],
            body=call["content"],
            options=LegacyWebhookHmacOptions(
                secret=plaintext_secret.encode("utf-8"),
                sender_identity="salesagent",
                now=time.time(),
            ),
        )
        assert verified.sender_identity == "salesagent"

        # 5. Body parses as the Sprint 6 envelope.
        envelope = json.loads(call["content"])
        assert envelope["event_type"] == "workflow.decided"
        assert envelope["tenant_id"] == tenant.tenant_id
        assert envelope["delivery_attempt"] == 1


# ---------------------------------------------------------------------------
# Event publication: workflow.decided fires on approve
# ---------------------------------------------------------------------------


class TestWorkflowDecidedPublication:
    def test_approve_workflow_fires_webhook(self, client, auth_headers, tenant, bound_factories, monkeypatch):
        # 1. Create a pending workflow on the tenant.
        principal = PrincipalFactory(tenant=tenant)
        ctx = ContextFactory(tenant=tenant, principal=principal)
        step = WorkflowStepFactory(context=ctx, status="pending", tool_name="create_media_buy")
        step_id = step.step_id
        ObjectWorkflowMappingFactory(workflow_step=step, object_type="media_buy", object_id="mb_subject")

        # 2. Register a subscription for workflow.decided.
        create = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={
                "url": "http://127.0.0.1:9999/hook",
                "event_types": ["workflow.decided"],
            },
        )
        assert create.status_code == 201
        plaintext_secret = create.get_json()["secret"]

        # 3. Patch the dispatch HTTP layer to capture deliveries.
        receiver = _MockReceiver()
        from src.admin.services import webhook_delivery

        async def fake_post_signed(url, secret, payload, extra_headers, *, client=None, timeout=10.0):
            from adcp.webhooks import sign_legacy_webhook

            headers, body = sign_legacy_webhook(secret, payload)
            headers["Content-Type"] = "application/json"
            response = await receiver.post(url, content=body, headers=headers)
            return response.status_code, 5, None

        monkeypatch.setattr(webhook_delivery, "_post_signed", fake_post_signed)

        # 4. Approve the workflow via the management API.
        approve = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{step_id}/approve",
            headers=auth_headers,
            json={"notes": "ok"},
        )
        assert approve.status_code == 200, approve.get_data(as_text=True)

        # 5. The receiver got the event; signature verifies.
        assert len(receiver.calls) == 1
        call = receiver.calls[0]
        verify_webhook_hmac(
            headers=call["headers"],
            body=call["content"],
            options=LegacyWebhookHmacOptions(
                secret=plaintext_secret.encode("utf-8"),
                sender_identity="salesagent",
                now=time.time(),
            ),
        )
        envelope = json.loads(call["content"])
        assert envelope["event_type"] == "workflow.decided"
        assert envelope["tenant_id"] == tenant.tenant_id
        # Payload carries the workflow detail with the decision.
        assert envelope["data"]["workflow"]["status"] == "approved"

    def test_no_subscribers_no_dispatch(self, client, auth_headers, tenant, bound_factories, monkeypatch):
        principal = PrincipalFactory(tenant=tenant)
        ctx = ContextFactory(tenant=tenant, principal=principal)
        step = WorkflowStepFactory(context=ctx, status="pending", tool_name="create_media_buy")
        step_id = step.step_id
        ObjectWorkflowMappingFactory(workflow_step=step, object_type="media_buy", object_id="mb_subject")

        receiver = _MockReceiver()
        from src.admin.services import webhook_delivery

        async def fake_post_signed(url, secret, payload, extra_headers, *, client=None, timeout=10.0):
            from adcp.webhooks import sign_legacy_webhook

            headers, body = sign_legacy_webhook(secret, payload)
            response = await receiver.post(url, content=body, headers=headers)
            return response.status_code, 5, None

        monkeypatch.setattr(webhook_delivery, "_post_signed", fake_post_signed)

        approve = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/workflows/{step_id}/approve",
            headers=auth_headers,
            json={},
        )
        assert approve.status_code == 200
        # No webhook subscribers → no dispatch.
        assert receiver.calls == []


# ---------------------------------------------------------------------------
# Expanded event catalog (#442): creative / principal / product events
# ---------------------------------------------------------------------------


class TestExpandedEventCatalog:
    """The schema declares the set of events the salesagent publishes.
    Adding to the catalog requires three things to line up:

    1. Schema accepts the event_type at subscription-create time
       (the ``WebhookEventType`` Literal is the gate).
    2. ``publish_event`` routes the event to matching subscribers
       (no special-casing needed beyond the type string).
    3. The full delivery path — envelope + HMAC + httpx — does not
       choke on the new type.

    These tests cover (1) and (3) for every new event in #442. Production
    callsites that fire each event are covered by their own tests in the
    blueprints / management API suites.
    """

    NEW_EVENT_TYPES = (
        "creative.status_changed",
        "principal.created",
        "product.created",
        "product.updated",
    )

    @pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
    def test_subscribe_to_new_event_type(self, client, auth_headers, tenant, bound_factories, event_type):
        """Every new event type is accepted at subscription-create time."""
        resp = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={"url": "http://127.0.0.1:9999/hook", "event_types": [event_type]},
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert event_type in body["event_types"]

    @pytest.mark.parametrize("event_type", NEW_EVENT_TYPES)
    def test_emit_event_delivers_to_subscriber(
        self, client, auth_headers, tenant, bound_factories, monkeypatch, event_type
    ):
        """A subscriber to each new event type receives a delivery when
        ``emit_event`` fires. Verifies the catalog → publisher → delivery
        path end-to-end for every new event."""
        create = client.post(
            f"/api/v1/tenant-management/tenants/{tenant.tenant_id}/webhooks",
            headers=auth_headers,
            json={"url": "http://127.0.0.1:9999/hook", "event_types": [event_type]},
        )
        assert create.status_code == 201
        plaintext_secret = create.get_json()["secret"]

        receiver = _MockReceiver()
        from src.admin.services import webhook_delivery
        from src.admin.services.webhook_publisher import emit_event

        async def fake_post_signed(url, secret, payload, extra_headers, *, client=None, timeout=10.0):
            from adcp.webhooks import sign_legacy_webhook

            headers, body = sign_legacy_webhook(secret, payload)
            headers["Content-Type"] = "application/json"
            response = await receiver.post(url, content=body, headers=headers)
            return response.status_code, 5, None

        monkeypatch.setattr(webhook_delivery, "_post_signed", fake_post_signed)

        emit_event(tenant.tenant_id, event_type, {"sample_field": "sample_value"})

        assert len(receiver.calls) == 1
        call = receiver.calls[0]
        verify_webhook_hmac(
            headers=call["headers"],
            body=call["content"],
            options=LegacyWebhookHmacOptions(
                secret=plaintext_secret.encode("utf-8"),
                sender_identity="salesagent",
                now=time.time(),
            ),
        )
        envelope = json.loads(call["content"])
        assert envelope["event_type"] == event_type
        assert envelope["tenant_id"] == tenant.tenant_id
        assert envelope["data"] == {"sample_field": "sample_value"}


# ---------------------------------------------------------------------------
# Repository / hashing primitives
# ---------------------------------------------------------------------------


class TestSecretHashing:
    def test_hash_secret_is_deterministic(self):
        assert hash_secret("abc") == hash_secret("abc")
        assert hash_secret("abc") != hash_secret("abd")
        # 64-char sha256 hex
        assert len(hash_secret("anything")) == 64

    def test_factory_stores_only_hash(self, integration_db, bound_factories):
        sub = WebhookSubscriptionFactory()
        # The DB row only has the hash, never the plaintext.
        assert len(sub.secret_hash) == 64
        # Plaintext is excluded from instantiation — the model has no
        # attribute to leak.
        assert not hasattr(sub, "_plaintext_secret")
