"""Persistence serialization helpers for workflow step payloads."""

from typing import Any


def serialize_for_workflow_step(model: Any) -> dict[str, Any]:
    """Serialize a Pydantic model for ``workflow_step.response_data`` storage.

    The ``workflow_step`` table stores request/response blobs in JSONB so
    approval reviewers can replay or inspect calls. JSONB needs a dict, but
    this is persistence bookkeeping, not a transport boundary. Keeping the
    dump in one module-level helper keeps ``_impl`` functions free of direct
    serialization calls and centralizes the JSON mode.
    """
    return model.model_dump(mode="json")
