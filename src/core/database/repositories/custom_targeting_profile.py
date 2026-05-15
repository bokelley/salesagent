"""CustomTargetingProfile repository — tenant-scoped data access.

Composable overlay for adapter-specific targeting (custom key-values,
adapter-opaque audience segment ids). AdCP-native targeting (geo, daypart,
device, contextual, standard demos) does NOT live here — it flows through
the create_media_buy request per spec.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core.database.models import CustomTargetingProfile


class CustomTargetingProfileRepository:
    """Tenant-scoped data access for CustomTargetingProfile."""

    _IMMUTABLE_FIELDS: frozenset[str] = frozenset({"tenant_id", "custom_targeting_profile_id", "id", "created_at"})

    def __init__(self, session: Session, tenant_id: str) -> None:
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def get_by_id(self, custom_targeting_profile_id: str) -> CustomTargetingProfile | None:
        return self._session.scalars(
            select(CustomTargetingProfile).where(
                CustomTargetingProfile.tenant_id == self._tenant_id,
                CustomTargetingProfile.custom_targeting_profile_id == custom_targeting_profile_id,
            )
        ).first()

    def list_by_ids(self, ids: list[str]) -> list[CustomTargetingProfile]:
        """Bulk-load by profile id. Order is not guaranteed to match the input."""
        if not ids:
            return []
        stmt = select(CustomTargetingProfile).where(
            CustomTargetingProfile.tenant_id == self._tenant_id,
            CustomTargetingProfile.custom_targeting_profile_id.in_(ids),
        )
        return list(self._session.scalars(stmt).all())

    def list_all(self, updated_since: datetime | None = None) -> list[CustomTargetingProfile]:
        stmt = select(CustomTargetingProfile).where(CustomTargetingProfile.tenant_id == self._tenant_id)
        if updated_since is not None:
            stmt = stmt.where(CustomTargetingProfile.updated_at > updated_since)
        return list(self._session.scalars(stmt.order_by(CustomTargetingProfile.custom_targeting_profile_id)).all())

    def add(self, profile: CustomTargetingProfile) -> None:
        if profile.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: profile.tenant_id={profile.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.add(profile)

    def delete(self, profile: CustomTargetingProfile) -> None:
        if profile.tenant_id != self._tenant_id:
            raise ValueError(
                f"tenant mismatch: profile.tenant_id={profile.tenant_id!r} != repo tenant_id={self._tenant_id!r}"
            )
        self._session.delete(profile)
