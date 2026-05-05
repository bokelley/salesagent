"""Factory_boy factories for Workflow-related models.

Sprint 3 of embedded-mode adds workflow approve/reject endpoints; integration
tests for the new endpoints need ORM rows for ``Context`` (the conversation
tracker that owns workflow steps), ``WorkflowStep``, and
``ObjectWorkflowMapping`` (step → subject linkage).
"""

from __future__ import annotations

import factory
from factory import LazyAttribute, Sequence, SubFactory

from src.core.database.models import Context as DBContext
from src.core.database.models import ObjectWorkflowMapping, WorkflowStep
from tests.factories.core import TenantFactory
from tests.factories.principal import PrincipalFactory


class ContextFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Conversation tracker — parent of WorkflowStep rows."""

    class Meta:
        model = DBContext
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    tenant = SubFactory(TenantFactory)
    principal = SubFactory(PrincipalFactory, tenant=factory.SelfAttribute("..tenant"))

    context_id = Sequence(lambda n: f"ctx_{n:04d}")
    tenant_id = LazyAttribute(lambda o: o.tenant.tenant_id)
    principal_id = LazyAttribute(lambda o: o.principal.principal_id)
    conversation_history = factory.LazyFunction(list)


class WorkflowStepFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Single workflow step — defaults to a pending media-buy approval."""

    class Meta:
        model = WorkflowStep
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    context = SubFactory(ContextFactory)

    step_id = Sequence(lambda n: f"step_{n:04d}")
    context_id = LazyAttribute(lambda o: o.context.context_id)
    step_type = "approval"
    tool_name = "create_media_buy"
    status = "pending"
    owner = "publisher"
    request_data = factory.LazyFunction(lambda: {"description": "Approve media buy"})
    comments = factory.LazyFunction(list)


class ObjectWorkflowMappingFactory(factory.alchemy.SQLAlchemyModelFactory):
    """Maps a workflow step to its subject object (e.g., media_buy → mb_xxx)."""

    class Meta:
        model = ObjectWorkflowMapping
        sqlalchemy_session = None
        sqlalchemy_session_persistence = "commit"

    workflow_step = SubFactory(WorkflowStepFactory)

    object_type = "media_buy"
    object_id = Sequence(lambda n: f"mb_{n:04d}")
    step_id = LazyAttribute(lambda o: o.workflow_step.step_id)
    action = "approve"
