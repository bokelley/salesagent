"""
Domain configuration utilities.

This module provides centralized domain configuration that can be customized
via environment variables, making the codebase vendor-neutral.

In single-tenant mode, most of these functions are not needed since there's
no subdomain routing. In multi-tenant mode, you must set SALES_AGENT_DOMAIN.
"""

import functools
import os


def _is_localhost(domain: str | None) -> bool:
    """Check if domain is localhost, 127.0.0.1, or a known local-DNS
    convenience domain (``localtest.me``, ``lvh.me``, and any subdomain
    of either). These all resolve to 127.0.0.1 via real public DNS, so
    they're treated as localhost for protocol auto-detection — the
    seller serves over http in dev regardless of which alias the user
    types into the browser.
    """
    if not domain:
        return False
    # Strip port if present
    host = domain.split(":")[0].lower()
    if host in ("localhost", "127.0.0.1"):
        return True
    # localtest.me + lvh.me are public-DNS aliases for 127.0.0.1.
    # Match the bare alias and any subdomain of it.
    for local_alias in ("localtest.me", "lvh.me"):
        if host == local_alias or host.endswith("." + local_alias):
            return True
    return False


def _get_protocol_for_domain(domain: str | None) -> str:
    """Return http for localhost, https for production domains."""
    return "http" if _is_localhost(domain) else "https"


@functools.lru_cache(maxsize=1)
def _resolve_single_tenant_virtual_host() -> str | None:
    """Look up the single tenant's ``virtual_host`` without disrupting other sessions.

    Cached for process lifetime — ``virtual_host`` changes via the Admin UI are
    rare. Call ``_resolve_single_tenant_virtual_host.cache_clear()`` from the
    Tenant edit handler if you need the next URL build to see the change
    without a restart.

    Uses a standalone ``Session(engine)`` rather than ``get_db_session()`` /
    the thread-local ``scoped_session`` so callers that already hold an open
    session (request handlers, services) aren't disrupted when this lookup's
    session closes.
    """
    if os.environ.get("ADCP_MULTI_TENANT", "false").lower() == "true":
        return None

    try:
        from sqlalchemy import text

        from src.core.database.database_session import get_engine

        # Single-column config lookup via Core SQL — no ORM models, no scoped
        # session, no repository needed. This is configuration access (read a
        # column to build URLs), not domain data access.
        with get_engine().connect() as conn:
            row = conn.execute(text("SELECT virtual_host FROM tenants WHERE is_active = TRUE LIMIT 1")).first()
            return row[0] if row and row[0] else None
    except Exception:
        return None


def get_sales_agent_domain() -> str | None:
    """Get the sales agent domain (e.g., sales-agent.example.com).

    Priority:
      1. ``SALES_AGENT_DOMAIN`` env var (explicit override — required in multi-tenant mode).
      2. In single-tenant mode, the active tenant's ``virtual_host``. This lets
         self-hosted publishers configure their domain once in the Admin UI
         without duplicating it into env vars.
      3. ``None`` if neither is available.
    """
    if env_domain := os.getenv("SALES_AGENT_DOMAIN"):
        return env_domain
    return _resolve_single_tenant_virtual_host()


def get_admin_domain() -> str | None:
    """Get the admin domain (e.g., admin.sales-agent.example.com).

    Returns:
        The configured ADMIN_DOMAIN, or constructs from SALES_AGENT_DOMAIN,
        or None if neither is configured.
    """
    # First check for explicit ADMIN_DOMAIN
    if domain := os.getenv("ADMIN_DOMAIN"):
        return domain
    # Fall back to constructing from sales agent domain if available
    if sales_domain := get_sales_agent_domain():
        return f"admin.{sales_domain}"
    return None


def get_super_admin_domain() -> str | None:
    """Get the domain for super admin emails (e.g., example.com).

    Returns:
        The configured SUPER_ADMIN_DOMAIN, or None if not configured.
    """
    return os.getenv("SUPER_ADMIN_DOMAIN")


def get_sales_agent_url(protocol: str = "https") -> str | None:
    """Get the full sales agent URL (e.g., https://sales-agent.example.com).

    Returns:
        The full URL, or None if SALES_AGENT_DOMAIN is not configured.
    """
    if domain := get_sales_agent_domain():
        return f"{protocol}://{domain}"
    return None


def get_admin_url(protocol: str = "https") -> str | None:
    """Get the full admin URL (e.g., https://admin.sales-agent.example.com).

    Returns:
        The full URL, or None if domain is not configured.
    """
    if domain := get_admin_domain():
        return f"{protocol}://{domain}"
    return None


def get_a2a_server_url(protocol: str | None = None) -> str | None:
    """Get the A2A server URL (e.g., https://sales-agent.example.com/a2a).

    Args:
        protocol: The protocol to use. If None, auto-detects based on domain
                  (http for localhost, https for production).

    Returns:
        The full URL, or None if SALES_AGENT_DOMAIN is not configured.
    """
    domain = get_sales_agent_domain()
    if not domain:
        return None
    # Auto-detect protocol if not specified
    if protocol is None:
        protocol = _get_protocol_for_domain(domain)
    if url := get_sales_agent_url(protocol):
        return f"{url}/a2a"
    return None


def get_mcp_server_url(protocol: str = "https") -> str | None:
    """Get the MCP server URL (e.g., https://sales-agent.example.com/mcp).

    Returns:
        The full URL, or None if SALES_AGENT_DOMAIN is not configured.
    """
    if url := get_sales_agent_url(protocol):
        return f"{url}/mcp"
    return None


def is_sales_agent_domain(host: str) -> bool:
    """
    Check if the given host is part of the sales agent domain.

    Args:
        host: The hostname to check (e.g., "tenant.sales-agent.example.com")

    Returns:
        True if the host ends with the sales agent domain.
        Returns False if SALES_AGENT_DOMAIN is not configured.
    """
    sales_domain = get_sales_agent_domain()
    if not sales_domain:
        return False
    return host.endswith(f".{sales_domain}") or host == sales_domain


def is_admin_domain(host: str) -> bool:
    """
    Check if the given host is the admin domain.

    Args:
        host: The hostname to check

    Returns:
        True if the host is the admin domain.
        Returns False if admin domain is not configured.
    """
    admin_domain = get_admin_domain()
    if not admin_domain:
        return False
    return host == admin_domain or host.startswith(f"{admin_domain}:")


def extract_subdomain_from_host(host: str) -> str | None:
    """
    Extract the subdomain from a host if it's a sales agent domain.

    Args:
        host: The hostname (e.g., "tenant.sales-agent.example.com")

    Returns:
        The subdomain (e.g., "tenant") or None if not a subdomain
        or if SALES_AGENT_DOMAIN is not configured.
    """
    sales_domain = get_sales_agent_domain()
    if not sales_domain:
        return None

    if f".{sales_domain}" in host:
        return host.split(f".{sales_domain}")[0]

    return None


def get_tenant_url(subdomain: str, protocol: str = "https") -> str | None:
    """
    Get the URL for a specific tenant subdomain.

    Args:
        subdomain: The tenant subdomain
        protocol: The protocol (http or https)

    Returns:
        The full tenant URL (e.g., https://tenant.sales-agent.example.com)
        or None if SALES_AGENT_DOMAIN is not configured.
    """
    if sales_domain := get_sales_agent_domain():
        return f"{protocol}://{subdomain}.{sales_domain}"
    return None


def get_oauth_redirect_uri(protocol: str = "https") -> str | None:
    """
    Get the OAuth redirect URI.

    Returns:
        The OAuth callback URL (e.g., https://sales-agent.example.com/admin/auth/google/callback)
        or None if not configured.
    """
    # Allow override via environment variable
    if env_uri := os.getenv("GOOGLE_OAUTH_REDIRECT_URI"):
        return env_uri

    if url := get_sales_agent_url(protocol):
        return f"{url}/admin/auth/google/callback"
    return None


def get_session_cookie_domain() -> str | None:
    """
    Get the session cookie domain (with leading dot for subdomain sharing).

    Returns:
        The cookie domain (e.g., ".sales-agent.example.com")
        or None if SALES_AGENT_DOMAIN is not configured.
    """
    if sales_domain := get_sales_agent_domain():
        return f".{sales_domain}"
    return None


def get_support_email() -> str:
    """
    Get the support email address for user-facing messages.

    Returns:
        The configured SUPPORT_EMAIL, or a placeholder if not set.
        Configure via environment variable for production deployments.
    """
    return os.getenv("SUPPORT_EMAIL", "support@example.com")
