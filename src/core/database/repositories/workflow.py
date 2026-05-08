"""Workflow repository — tenant-scoped data access for workflow step tables.

Covers three ORM models:
- WorkflowStep: individual steps/tasks in a workflow
- ObjectWorkflowMapping: maps workflow steps to business objects
- Context (DBContext): conversation tracker for async operations

Core invariant: every query includes tenant_id in the WHERE clause (via Context join).
The tenant_id is set at construction time and injected into all queries automatically.

Write methods add objects to the session but never commit — the caller (or UoW)
handles commit/rollback at the boundary.

beads: salesagent-4d4
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, asc, case, func, or_, select
from sqlalchemy.orm import Session

from src.core.database.models import Context as DBContext
from src.core.database.models import ObjectWorkflowMapping, Principal, WorkflowStep


class WorkflowRepository:
    """Tenant-scoped data access for WorkflowStep and ObjectWorkflowMapping.

    All queries filter by tenant_id (via Context join) automatically. Write
    methods modify the session but never commit — the Unit of Work handles that.

    Args:
        session: SQLAlchemy session (caller manages lifecycle).
        tenant_id: Tenant scope for all queries.
    """

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    # ------------------------------------------------------------------
    # WorkflowStep reads
    # ------------------------------------------------------------------

    def get_by_step_id(self, step_id: str) -> WorkflowStep | None:
        """Get a workflow step by its ID within the tenant."""
        return self._session.scalars(
            select(WorkflowStep)
            .join(DBContext)
            .where(
                WorkflowStep.step_id == step_id,
                DBContext.tenant_id == self._tenant_id,
            )
        ).first()

    def list_by_tenant(
        self,
        *,
        status: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[WorkflowStep]:
        """List workflow steps for the tenant, with optional filters.

        Args:
            status: Filter by step status (e.g., "pending", "requires_approval").
            object_type: Filter by associated object type (e.g., "media_buy").
            object_id: Filter by specific object ID (requires object_type).
            offset: Number of steps to skip.
            limit: Maximum number of steps to return.
        """
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(
                DBContext.tenant_id == self._tenant_id,
            )
        )

        if status:
            stmt = stmt.where(WorkflowStep.status == status)

        if object_type and object_id:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
            )
        elif object_type:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
            )

        stmt = stmt.order_by(WorkflowStep.created_at.desc()).offset(offset).limit(limit)
        return list(self._session.scalars(stmt).all())

    def count_by_tenant(
        self,
        *,
        status: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
    ) -> int:
        """Count workflow steps matching the given filters.

        Uses the same filter logic as list_by_tenant but returns only the count.
        """
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(
                DBContext.tenant_id == self._tenant_id,
            )
        )

        if status:
            stmt = stmt.where(WorkflowStep.status == status)

        if object_type and object_id:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
            )
        elif object_type:
            stmt = stmt.join(ObjectWorkflowMapping).where(
                ObjectWorkflowMapping.object_type == object_type,
            )

        result = self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        return result or 0

    # ------------------------------------------------------------------
    # ObjectWorkflowMapping reads
    # ------------------------------------------------------------------

    def get_latest_mapping_for_object(self, object_type: str, object_id: str) -> ObjectWorkflowMapping | None:
        """Get the most recent workflow mapping for a specific object within the tenant."""
        return self._session.scalars(
            select(ObjectWorkflowMapping)
            .join(WorkflowStep, ObjectWorkflowMapping.step_id == WorkflowStep.step_id)
            .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
            .where(
                ObjectWorkflowMapping.object_type == object_type,
                ObjectWorkflowMapping.object_id == object_id,
                DBContext.tenant_id == self._tenant_id,
            )
            .order_by(ObjectWorkflowMapping.created_at.desc())
        ).first()

    def get_step_by_id(self, step_id: str) -> WorkflowStep | None:
        """Get a workflow step by its primary key within the tenant."""
        return self._session.scalars(
            select(WorkflowStep)
            .join(DBContext)
            .where(
                WorkflowStep.step_id == step_id,
                DBContext.tenant_id == self._tenant_id,
            )
        ).first()

    def find_by_idempotency_key(
        self,
        idempotency_key: str,
        principal_id: str,
        tool_name: str,
    ) -> WorkflowStep | None:
        """Find an existing workflow step by ``idempotency_key`` within (tenant, principal, tool).

        Defence-in-depth on the SDK's post-hoc ``IdempotencyStore.wrap``. The
        SDK caches the response after the handler completes, so two
        sequential same-key calls hitting the impl before the first commits
        both reach the same code path. Returning the earliest matching
        step lets the impl replay deterministically — no additional state
        is mutated, no second adapter call is made.

        Args:
            idempotency_key: Buyer-supplied key extracted from
                ``request_data["idempotency_key"]``.
            principal_id: Caller's principal id (scoped via ``Context``).
            tool_name: ``WorkflowStep.tool_name`` to match (e.g.
                ``"update_media_buy"``).

        Returns the earliest step matching the key, or ``None``.
        """
        return self._session.scalars(
            select(WorkflowStep)
            .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
            .where(
                DBContext.tenant_id == self._tenant_id,
                DBContext.principal_id == principal_id,
                WorkflowStep.tool_name == tool_name,
                WorkflowStep.request_data["idempotency_key"].as_string() == idempotency_key,
            )
            .order_by(asc(WorkflowStep.created_at))
        ).first()

    def get_mappings_for_step(self, step_id: str) -> list[ObjectWorkflowMapping]:
        """Get all object mappings for a workflow step within the tenant."""
        return list(
            self._session.scalars(
                select(ObjectWorkflowMapping)
                .join(WorkflowStep, ObjectWorkflowMapping.step_id == WorkflowStep.step_id)
                .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
                .where(
                    ObjectWorkflowMapping.step_id == step_id,
                    DBContext.tenant_id == self._tenant_id,
                )
            ).all()
        )

    def get_mappings_for_steps(self, step_ids: list[str]) -> dict[str, list[ObjectWorkflowMapping]]:
        """Get object mappings for multiple workflow steps within the tenant.

        Returns a dict mapping step_id -> list of ObjectWorkflowMapping.
        """
        if not step_ids:
            return {}

        mappings = list(
            self._session.scalars(
                select(ObjectWorkflowMapping)
                .join(WorkflowStep, ObjectWorkflowMapping.step_id == WorkflowStep.step_id)
                .join(DBContext, WorkflowStep.context_id == DBContext.context_id)
                .where(
                    ObjectWorkflowMapping.step_id.in_(step_ids),
                    DBContext.tenant_id == self._tenant_id,
                )
            ).all()
        )

        result: dict[str, list[ObjectWorkflowMapping]] = {sid: [] for sid in step_ids}
        for mapping in mappings:
            result[mapping.step_id].append(mapping)
        return result

    def list_filtered_with_cursor(
        self,
        *,
        statuses: list[str] | None = None,
        workflow_type: str | None = None,
        cursor_created_at: datetime | None = None,
        cursor_id: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowStep]:
        """List workflow steps for ``GET /workflows`` drill-down.

        Sort: pending-equivalent statuses first (``pending``, ``in_progress``,
        ``requires_approval``), then by ``created_at desc, step_id desc``.

        Cursor pagination uses the secondary ordering ``(created_at, step_id)``
        within the pending-vs-decided partition; the cursor row's pending bit
        is encoded in the cursor caller-side so we just compare on the tuple.

        Args:
            statuses: Restrict to these step statuses (raw strings as stored
                in the DB — ``pending``, ``requires_approval``, ``completed``,
                ``failed``, ``cancelled``, ...).
            workflow_type: Filter on either ``tool_name`` or ``step_type``
                matching this value.
            cursor_created_at, cursor_id: Bookmark for the next page.
            limit: Page size.
        """
        # Pending-first sort: 0 for "open" rows, 1 for everything else.
        open_states = ("pending", "in_progress", "requires_approval")
        pending_priority = case((WorkflowStep.status.in_(open_states), 0), else_=1)

        stmt = select(WorkflowStep).join(DBContext).where(DBContext.tenant_id == self._tenant_id)

        if statuses:
            stmt = stmt.where(WorkflowStep.status.in_(statuses))
        if workflow_type:
            stmt = stmt.where(
                or_(
                    WorkflowStep.tool_name == workflow_type,
                    WorkflowStep.step_type == workflow_type,
                )
            )

        if cursor_created_at is not None and cursor_id is not None:
            # Strict-less-than on the (created_at, step_id) tuple — same
            # pattern as the audit log repo. Pending-first ordering is
            # uniform across pages so the partition flip happens naturally
            # when we run out of pending rows; callers don't need to encode
            # the bit in the cursor.
            stmt = stmt.where(
                or_(
                    WorkflowStep.created_at < cursor_created_at,
                    and_(
                        WorkflowStep.created_at == cursor_created_at,
                        WorkflowStep.step_id < cursor_id,
                    ),
                )
            )

        stmt = stmt.order_by(
            asc(pending_priority),
            WorkflowStep.created_at.desc(),
            WorkflowStep.step_id.desc(),
        ).limit(limit)
        return list(self._session.scalars(stmt).all())

    def get_context_principal(self, step: WorkflowStep) -> tuple[str | None, str | None]:
        """Return ``(principal_id, principal_name)`` for the requesting principal of a step.

        Reads the step's Context to find the principal that opened the
        workflow. Returns ``(None, None)`` if the context is missing (legacy
        rows) or the principal lookup fails.
        """
        ctx = self._session.scalars(
            select(DBContext).filter_by(context_id=step.context_id, tenant_id=self._tenant_id)
        ).first()
        if ctx is None:
            return None, None
        name = self.get_principal_name(ctx.principal_id)
        return ctx.principal_id, name

    def get_all_steps(self, *, limit: int | None = None) -> list[WorkflowStep]:
        """Get all workflow steps for this tenant, newest first."""
        stmt = (
            select(WorkflowStep)
            .join(DBContext)
            .where(DBContext.tenant_id == self._tenant_id)
            .order_by(WorkflowStep.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt).all())

    # ------------------------------------------------------------------
    # ObjectWorkflowMapping writes
    # ------------------------------------------------------------------

    def add_mapping(
        self,
        *,
        step_id: str,
        object_type: str,
        object_id: str,
        action: str,
    ) -> ObjectWorkflowMapping:
        """Create and add an ObjectWorkflowMapping to the session.

        Does NOT commit — the caller (or UoW) handles that.
        """
        mapping = ObjectWorkflowMapping(
            step_id=step_id,
            object_type=object_type,
            object_id=object_id,
            action=action,
        )
        self._session.add(mapping)
        return mapping

    # ------------------------------------------------------------------
    # Principal reads (for audit logging)
    # ------------------------------------------------------------------

    def get_principal_name(self, principal_id: str) -> str | None:
        """Look up a principal's display name within the tenant.

        Returns the name string, or None if the principal is not found.
        """
        principal = self._session.scalars(
            select(Principal).filter_by(
                tenant_id=self._tenant_id,
                principal_id=principal_id,
            )
        ).first()
        return principal.name if principal else None

    # ------------------------------------------------------------------
    # WorkflowStep writes
    # ------------------------------------------------------------------

    def update_status(
        self,
        step_id: str,
        *,
        status: str,
        completed_at: datetime | None = None,
        response_data: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> WorkflowStep | None:
        """Update the status of a workflow step.

        Returns the updated step, or None if not found.
        Does NOT commit — the caller handles that.
        """
        step = self.get_by_step_id(step_id)
        if step is None:
            return None

        step.status = status
        if completed_at is not None:
            step.completed_at = completed_at
        if response_data is not None:
            step.response_data = response_data
        if error_message is not None:
            step.error_message = error_message
        elif status == "completed":
            # Clear error message on successful completion
            step.error_message = None

        self._session.flush()
        return step
