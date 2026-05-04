"""Adopter implementations of the framework's store Protocols.

All stores share the salesagent ORM (``src.core.database.models``) so the
greenfield and legacy stacks operate on the same DB rows through one source
of truth.

Stores:
- ``accounts.py``  — ``AccountStore`` (resolve principal → account)
- ``media_buy.py`` — ``MediaBuyStore`` (targeting_overlay echo persistence)
- ``tenants.py``   — Tenant lookup for the subdomain router
- ``audit.py``     — ``AuditSink`` over the audit_logs table
"""
