"""AuditSink over the existing ``audit_logs`` table.

The framework's ``AuditSink`` Protocol records skill-dispatch events (one
per tool/A2A call) — the seller-facing observability trail. Wired via
``make_audit_middleware`` and added to the SkillMiddleware chain.

NOTE: per the framework's own warning, this is **not** sufficient for SOX
or GDPR Article 30 compliance — the in-process sink is best-effort and
drops on failure. For compliance-of-record, write to a separate durable
store outside the request hot path.

Skeleton.
"""

from __future__ import annotations


# from adcp.audit_sink import AuditSink, make_audit_middleware
# from src.core.database.models import AuditLog


# class SalesagentAuditSink:
#     async def record(self, event): ...
