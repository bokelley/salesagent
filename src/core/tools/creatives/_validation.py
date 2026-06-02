"""Creative input validation: schema and business rule checks."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from src.core.canonical_formats import canonicalize_format_ref
from src.core.schemas import Creative, CreativeAsset, CreativePolicy, CreativeStatusEnum
from src.core.validation_helpers import run_async_in_sync_context

logger = logging.getLogger(__name__)


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Get a field from a model or dict (transitional helper for Phase 1a).

    Removed in Phase 1b when all callers pass typed models.
    """
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)


def _validate_creative_input(
    creative: CreativeAsset,
    registry: Any,
    principal_id: str,
    registered_agent_urls: set[str] | None = None,
) -> Creative:
    """Validate a CreativeAsset and return a validated Creative model.

    Builds schema_data from the creative model, validates via Creative(**schema_data),
    checks business logic (empty name, missing format), and validates the format_id
    against the creative agent registry.

    Args:
        creative: CreativeAsset model from the sync payload.
        registry: CreativeAgentRegistry instance for format validation.
        principal_id: Authenticated principal ID for ownership.
        registered_agent_urls: Normalized HTTP creative-agent URLs allowed for this tenant.

    Returns:
        Validated Creative schema object.

    Raises:
        ValidationError: If the creative fails Pydantic schema validation.
        ValueError: If business logic checks fail (empty name, missing format,
            unknown format, unreachable agent).
    """
    # Create temporary schema object for validation (AdCP v1 spec compliant)
    # Only include AdCP spec fields + internal fields
    schema_data: dict[str, Any] = {
        "creative_id": creative.creative_id or str(uuid.uuid4()),
        "name": creative.name,
        "format_id": canonicalize_format_ref(creative.format_id),
        "assets": creative.assets or {},  # Required by AdCP v1 spec
        # adcp 3.6.0: variants is required by Creative schema (list[CreativeVariant]).
        # CreativeAsset (sync payload) may carry variants as an extra field (extra="allow").
        # New creatives start with no variants yet (empty list is valid per spec).
        "variants": getattr(creative, "variants", []) or [],
        # Internal fields (added by sales agent)
        "principal_id": principal_id,
        "created_date": datetime.now(UTC),
        "updated_date": datetime.now(UTC),
        "status": CreativeStatusEnum.pending_review.value,
    }

    # Add optional AdCP v1 fields if provided
    # NOTE: creative.inputs is NOT included — Creative model (extra="forbid")
    # doesn't accept it. Processing reads inputs from the original CreativeAsset.
    if creative.tags:
        schema_data["tags"] = creative.tags
    approved = getattr(creative, "approved", None)
    if approved is not None:
        schema_data["approved"] = approved

    # Pass through AI provenance metadata (EU AI Act Article 50)
    # Library Provenance model must be converted to dict — our local Provenance
    # is not a subclass and Pydantic rejects cross-hierarchy model instances.
    provenance = getattr(creative, "provenance", None)
    if provenance is not None:
        from pydantic import BaseModel

        schema_data["provenance"] = (
            provenance.model_dump(mode="json") if isinstance(provenance, BaseModel) else provenance
        )

    # Validate by creating a Creative schema object
    # This will fail if required fields are missing or invalid (like empty name)
    # Also auto-upgrades string format_ids to FormatId objects via validator
    validated_creative = Creative(**schema_data)

    # Additional business logic validation
    if not creative.name or str(creative.name).strip() == "":
        raise ValueError("Creative name cannot be empty")

    if not creative.format_id:
        raise ValueError("Creative format is required")

    # Use validated format (auto-upgraded from string if needed)
    format_value = validated_creative.format

    if format_value is None:
        raise ValueError(f"Creative format '{creative.format_id}' could not be resolved")

    # Validate format exists in creative agent
    agent_url = str(format_value.agent_url)
    format_id = format_value.id

    # Skip external validation for adapter-provided formats (non-HTTP URLs).
    # These formats are served by an adapter-specific creative surface.
    # and validation is handled internally by the adapter
    is_adapter_format = not agent_url.startswith(("http://", "https://"))

    if not is_adapter_format:
        from src.core.validation import normalize_agent_url

        normalized_agent_url = normalize_agent_url(agent_url)
        if registered_agent_urls is not None and normalized_agent_url not in registered_agent_urls:
            raise ValueError(
                f"Creative agent '{agent_url}' is not registered for this tenant. "
                f"Use list_creative_formats to discover supported formats."
            )

        # Check if format exists (uses in-memory cache with 1-hour TTL)
        # Use run_async_in_sync_context to handle both sync and async contexts
        format_spec = None
        validation_error = None

        try:
            format_spec = run_async_in_sync_context(registry.get_format(agent_url, format_id))
        except Exception as e:
            # Network error, agent unreachable, etc.
            validation_error = e
            logger.warning(
                f"Failed to fetch format '{format_id}' from agent {agent_url}: {e}",
                exc_info=True,
            )

        if validation_error:
            # Agent unreachable or network error
            raise ValueError(
                f"Cannot validate format '{format_id}': Creative agent at {agent_url} "
                f"is unreachable or returned an error. Please verify the agent URL is correct "
                f"and the agent is running. Error: {str(validation_error)}"
            )
        elif not format_spec:
            # Format not found (agent is reachable but format doesn't exist)
            raise ValueError(
                f"Unknown format '{format_id}' from agent {agent_url}. "
                f"Format must be registered with the creative agent. "
                f"Use list_creative_formats to see available formats."
            )
        # TODO(#767): Call validate_creative when available in creative agent spec
        # to validate that creative manifest matches format requirements
    else:
        logger.debug(f"Skipping external validation for adapter-provided format '{format_id}' (agent_url: {agent_url})")

    return validated_creative


def get_registered_creative_agent_urls(registry: Any, tenant_id: str | None) -> set[str] | None:
    """Return normalized tenant-registered creative-agent URLs when available."""
    if not tenant_id:
        return None

    get_tenant_agents = getattr(registry, "_get_tenant_agents", None)
    if not callable(get_tenant_agents):
        return None

    agents = get_tenant_agents(tenant_id)
    if not isinstance(agents, list | tuple):
        return None

    from src.core.canonical_formats import DEFAULT_CREATIVE_AGENT_URL
    from src.core.validation import normalize_agent_url

    registered = {normalize_agent_url(agent.agent_url) for agent in agents if getattr(agent, "enabled", True)}

    default_agent = getattr(registry, "DEFAULT_AGENT", None)
    default_agent_url = getattr(default_agent, "agent_url", None)
    if default_agent_url and normalize_agent_url(default_agent_url) in registered:
        # The reference creative agent may be configured to a local/service URL in
        # CI or deployments, while products expose the public canonical AdCP URL.
        registered.add(DEFAULT_CREATIVE_AGENT_URL)

    return registered


def check_provenance_required(
    creative: Creative,
    creative_policy: CreativePolicy | dict | None,
) -> str | None:
    """Check if provenance metadata is required but missing.

    Args:
        creative: Validated Creative schema object.
        creative_policy: Product's creative policy (may be dict from DB).

    Returns:
        Warning message if provenance is required but missing, None otherwise.
    """
    if creative_policy is None:
        return None

    # Handle both CreativePolicy model and dict from DB
    if isinstance(creative_policy, dict):
        provenance_required = creative_policy.get("provenance_required")
    else:
        provenance_required = creative_policy.provenance_required

    if not provenance_required:
        return None

    if creative.provenance is None:
        return (
            "AI provenance metadata is required by product creative policy "
            "but not provided. Creative flagged for review."
        )

    return None
