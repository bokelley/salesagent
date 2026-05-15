"""Typed CRUD over the SpringServe Campaigns API.

Endpoint reference:
- POST   /api/v0/campaigns
- GET    /api/v0/campaigns/{id}
- PUT    /api/v0/campaigns/{id}
- DELETE /api/v0/campaigns/{id}

Wire shapes captured from a live Talpa account on 2026-05-14; see
``entities.Campaign`` for the field set we surface.
"""

from __future__ import annotations

from typing import Any

from src.adapters.springserve._transport import SpringServeTransport
from src.adapters.springserve.entities import Campaign


class SpringServeCampaignsClient:
    """Campaign CRUD bound to one :class:`SpringServeTransport`."""

    def __init__(self, transport: SpringServeTransport):
        self._transport = transport

    def create(
        self,
        *,
        name: str,
        demand_partner_id: int,
        is_active: bool = False,
        code: str | None = None,
        secondary_code: str | None = None,
        note: str | None = None,
        rate_currency: str = "USD",
        cost_model_type: int = 0,
    ) -> Campaign:
        """POST a new Campaign and return the parsed entity.

        Campaigns are created paused (``is_active=False``) so the operator
        can verify configuration before unleashing demand. Flip via
        :meth:`update` once creative + demand tags are wired.
        """
        body: dict[str, Any] = {
            "name": name,
            "demand_partner_id": demand_partner_id,
            "is_active": is_active,
            "rate_currency": rate_currency,
            "cost_model_type": cost_model_type,
        }
        if code is not None:
            body["code"] = code
        if secondary_code is not None:
            body["secondary_code"] = secondary_code
        if note is not None:
            body["note"] = note
        response = self._transport.post_json("/campaigns", body)
        return Campaign.model_validate(response)

    def get(self, campaign_id: int) -> Campaign:
        response = self._transport.get_json(f"/campaigns/{campaign_id}")
        return Campaign.model_validate(response)

    def update(self, campaign_id: int, *, is_active: bool | None = None, **fields: Any) -> Campaign:
        """PUT changes to a Campaign. ``is_active`` is the most common toggle
        (campaign-level pause/resume); arbitrary other fields can be passed
        through via kwargs."""
        body: dict[str, Any] = dict(fields)
        if is_active is not None:
            body["is_active"] = is_active
        response = self._transport.put_json(f"/campaigns/{campaign_id}", body)
        return Campaign.model_validate(response)

    def delete(self, campaign_id: int) -> None:
        """DELETE a Campaign. SpringServe cascades to its demand tags."""
        self._transport.delete_json(f"/campaigns/{campaign_id}")
