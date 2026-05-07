"""Factory_boy factories for signing-related models.

Per-buyer-agent signing model: TenantSigningPolicy + TenantSigningCredential.
Operator/admit factories were dropped when the per-agent trust model
landed (slice 2 of signing-non-embedded refactor).
"""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import (
    TenantSigningCredential,
    TenantSigningPolicy,
)
from tests.factories.core import TenantFactory


class TenantSigningPolicyFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = TenantSigningPolicy
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ("tenant",)

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    enabled = False
    required_for = factory.LazyFunction(list)
    covers_digest_policy = "either"
    max_skew_seconds = 60
    max_window_seconds = 300


class TenantSigningCredentialFactory(factory.alchemy.SQLAlchemyModelFactory):
    class Meta:
        model = TenantSigningCredential
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"
        exclude = ("tenant",)

    tenant = SubFactory(TenantFactory)
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    purpose = "webhook-signing"
    backend = "local_pem"
    backend_ref = LazyAttribute(lambda o: f"/tmp/signing-keys/{o.tenant_id}.pem")
    key_id = Sequence(lambda n: f"kid-{n:04d}")
    public_jwk = factory.LazyFunction(lambda: {"kty": "OKP", "crv": "Ed25519", "x": "test-public-key-bytes"})
    is_active = True
