from unittest.mock import AsyncMock, patch

import pytest
from adcp import AdagentsValidationError

from src.services.adagents_fetch import fetch_adagents_permissive


@pytest.mark.asyncio
async def test_fetch_adagents_permissive_allows_bare_agent_entries():
    raw = {"authorized_agents": [{"url": "https://interchange.io"}], "properties": []}
    with (
        patch(
            "src.services.adagents_fetch.fetch_adagents",
            AsyncMock(side_effect=AdagentsValidationError("Agent authorization must have exactly one of: properties")),
        ),
        patch("src.services.adagents_fetch._fetch_direct_json", AsyncMock(return_value=raw)) as fallback,
    ):
        result = await fetch_adagents_permissive("wonderstruck.org")

    assert result == raw
    fallback.assert_awaited_once_with("wonderstruck.org", timeout=10.0, user_agent="AdCP-Client/1.0")


@pytest.mark.asyncio
async def test_fetch_adagents_permissive_reraises_other_validation_errors():
    with (
        patch(
            "src.services.adagents_fetch.fetch_adagents",
            AsyncMock(side_effect=AdagentsValidationError("Invalid JSON in adagents.json")),
        ),
        patch("src.services.adagents_fetch._fetch_direct_json", AsyncMock()) as fallback,
    ):
        with pytest.raises(AdagentsValidationError, match="Invalid JSON"):
            await fetch_adagents_permissive("publisher.example")

    fallback.assert_not_awaited()
