"""Inject the canonical v3 envelope ``status`` field on get_adcp_capabilities.

AdCP 3.0.11's protocol-envelope schema requires a top-level ``status``
field on every response. ``capabilities_response()`` in the upstream SDK
(adcp.server.responses) emits the response body without an envelope
``status``, so the wire is technically out of spec — buyer tooling that
walks ``protocol-envelope.json`` rejects the response, and the storyboard
``v3_envelope_integrity / no_legacy_status_fields`` step fails.

Importing this module monkey-patches
:meth:`adcp.decisioning.handler.PlatformHandler.get_adcp_capabilities` to
append ``status="completed"`` to the dict the SDK returns. Discovery is
synchronous so completion is always the correct task state.

Remove this shim once the upstream SDK adds envelope status itself.
"""

from __future__ import annotations

from typing import Any

from adcp.decisioning.handler import PlatformHandler

_ORIGINAL = PlatformHandler.get_adcp_capabilities


async def _get_adcp_capabilities_with_envelope_status(
    self: PlatformHandler,
    params: Any = None,
    context: Any = None,
) -> dict[str, Any]:
    result = await _ORIGINAL(self, params, context)
    if isinstance(result, dict) and "status" not in result:
        result["status"] = "completed"
    return result


PlatformHandler.get_adcp_capabilities = _get_adcp_capabilities_with_envelope_status  # type: ignore[method-assign]
