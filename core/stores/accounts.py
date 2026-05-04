"""AccountStore over the salesagent ``principals`` table.

The framework's ``AccountStore`` Protocol resolves verified-principal opaque
strings to ``Account`` records. The reference impls
(``SingletonAccounts``/``ExplicitAccounts``/``FromAuthAccounts``) cover most
shapes — for salesagent's principal-token auth, ``FromAuthAccounts`` plus a
custom resolver works; or we implement the Protocol directly.

The resolved Account's ``metadata['tenant_id']`` is what
``PlatformRouter`` reads to pick which per-tenant ``DecisioningPlatform``
handles the request.

Skeleton.
"""

from __future__ import annotations


# from adcp.decisioning.accounts import AccountStore
# from adcp.decisioning.types import Account
# from src.core.database.database_session import get_db_session
# from src.core.database.models import Principal


# class SalesagentAccountStore:
#     """Resolves auth_info.principal (= principal_id) to an Account whose
#     metadata carries the tenant_id for PlatformRouter dispatch."""
#
#     async def resolve(self, ctx) -> Account | None:
#         principal_id = ctx.auth_info.principal
#         with get_db_session() as session:
#             row = session.scalars(
#                 select(Principal).filter_by(principal_id=principal_id)
#             ).first()
#         if row is None:
#             return None
#         return Account(
#             id=row.principal_id,
#             metadata={"tenant_id": row.tenant_id},
#         )
