"""Helpers for tests that need to opt into the SDK's current explicit AdCP beta."""

from adcp.validation.envelope import get_supported_adcp_versions


def explicit_adcp_version() -> str:
    """Return the current explicit 3.x AdCP version advertised by the SDK."""
    versions = [version for version in get_supported_adcp_versions() if version != "3.0" and version.startswith("3.")]
    if not versions:
        raise AssertionError("SDK did not advertise an explicit 3.x AdCP version")
    return versions[0]
