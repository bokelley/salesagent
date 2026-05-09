"""Regression test: ``_coerce_to_request_model`` must filter framework-injected
fields not declared on the impl-local schema.

Issue #273: ``get_media_buys`` raises ``INTERNAL_ERROR: ... raised
ValidationError; see details for cause`` when invoked from the AdCP
storyboard (or any spec-compliant client). The framework's
``GetMediaBuysRequest`` carries ``adcp_major_version=3``,
``include_snapshot=False``, ``include_history=0`` (defaults from the
library schema). Our impl-local ``GetMediaBuysRequest`` deliberately
doesn't expose these fields (transport-only flags policy, see
``src/core/schemas/_base.py`` docstring), so dev-mode ``extra='forbid'``
rejects them and the request never reaches the impl.

The fix filters the dumped dict to keys the target ``model_cls``
actually declares. This preserves the explicit-rejection semantics for
genuinely unknown buyer-supplied fields while making the framework→impl
hop robust to upstream schema growth.
"""

from __future__ import annotations

import pytest


def test_coerce_filters_framework_extras_for_get_media_buys() -> None:
    """Framework-injected fields must be filtered, not raise ValidationError."""
    from adcp.types import GetMediaBuysRequest as LibraryGetMediaBuysRequest

    from core.platforms._delegate import _coerce_to_request_model
    from src.core.schemas import GetMediaBuysRequest as LocalGetMediaBuysRequest

    # Build a library-shape request the way the framework hands it to the
    # delegate — defaults populate the transport-only flags.
    lib_req = LibraryGetMediaBuysRequest(media_buy_ids=["mb_1"])
    assert lib_req.include_snapshot is False, "library schema must populate the default we filter"

    # Coerce to our local schema — must NOT raise on extra fields.
    local_req = _coerce_to_request_model(lib_req, LocalGetMediaBuysRequest)

    assert isinstance(local_req, LocalGetMediaBuysRequest)
    assert local_req.media_buy_ids == ["mb_1"]
    assert not hasattr(local_req, "include_snapshot"), (
        "include_snapshot is transport-only — must not propagate to local schema"
    )


def test_coerce_dict_input_still_strict() -> None:
    """Dict input bypasses the filter — strict dev-mode validation still applies.

    Direct dict input is for callers who control the wire shape (tests, internal
    callers); they shouldn't get a silent drop on a typo.
    """
    from pydantic import ValidationError

    from core.platforms._delegate import _coerce_to_request_model
    from src.core.schemas import GetMediaBuysRequest

    with pytest.raises(ValidationError):
        _coerce_to_request_model({"media_buy_ids": ["x"], "definitely_not_a_field": True}, GetMediaBuysRequest)
