"""Auth: extract verified principal from request → AccountStore lookup.

Replaces ``src/core/auth.py`` + ``resolved_identity.py`` + ``auth_middleware.py``.

The framework's seam is ``ToolContext.auth_info.principal`` (an opaque string
the auth middleware writes onto the dispatch context). For salesagent's
existing principal-token auth, the middleware reads ``x-adcp-auth`` (MCP) or
the A2A bearer header, looks up ``Principal.access_token``, and writes the
``principal_id`` onto ``auth_info``.

The ``AccountStore`` then resolves principal → account using
``FromAuthAccounts`` (resolution='implicit') — the third reference impl in
``adcp.decisioning.accounts``.

Skeleton.
"""

from __future__ import annotations


# from adcp.decisioning.accounts import FromAuthAccounts
# from src.core.database.database_session import get_db_session
# from src.core.database.models import Principal


# def build_auth_middleware():
#     """Starlette middleware: validate token, set auth_info.principal.
#
#     Pseudocode::
#
#         async def dispatch(request, call_next):
#             token = request.headers.get("x-adcp-auth") or extract_a2a_bearer(...)
#             principal = lookup_principal_by_token(token)
#             if not principal:
#                 return PlainTextResponse("unauthorized", status_code=401)
#             # Set request-scoped contextvar that build_context reads
#             auth_info_var.set(AuthInfo(principal=principal.principal_id))
#             return await call_next(request)
#     """
#     ...


# def build_account_store():
#     """FromAuthAccounts wired against the principals table."""
#     ...
