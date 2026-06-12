import httpx
import pytest

from src.admin.services.webhook_delivery import _post_signed


class _FailingClient:
    async def post(self, url, *, content, headers):
        del url, content, headers
        request = httpx.Request("POST", "https://buyer.example/webhook?token=secret&state=oauth-state")
        raise httpx.ConnectError(
            "connection refused for https://buyer.example/webhook?token=secret&state=oauth-state",
            request=request,
        )


@pytest.mark.asyncio
async def test_post_signed_transport_error_preserves_failure_mode_and_scrubs_url():
    status_code, _latency_ms, error = await _post_signed(
        "https://buyer.example/webhook?token=secret",
        "signing-secret",
        {"event_id": "evt_1"},
        None,
        client=_FailingClient(),
    )

    assert status_code is None
    assert error == "ConnectError: connection refused for [url]"
    assert "buyer.example" not in error
    assert "secret" not in error
    assert "oauth-state" not in error
