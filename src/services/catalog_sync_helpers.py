"""Shared helpers for catalog background sync jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypeVar

from src.core.database.database_session import get_db_session
from src.core.database.repositories.sync_job import SyncJobRepository
from src.services.adapter_sync_orchestration import _sanitize_error_message

T = TypeVar("T")


@dataclass
class CatalogSyncResult:
    sync_id: str
    tenant_id: str
    started_at: datetime
    finished_at: datetime
    succeeded: bool
    counts: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def new_sync_id() -> str:
    return f"sync_{uuid.uuid4().hex[:16]}"


def create_running_catalog_sync_job(
    *,
    tenant_id: str,
    sync_id: str,
    sync_type: str,
    triggered_by: str,
    triggered_by_id: str | None,
    started_at: datetime,
    date_range: str,
    line_item_types: list[str],
) -> None:
    with get_db_session() as session:
        session.info["platform_background_worker"] = True
        SyncJobRepository(session, tenant_id).create_running(
            sync_id=sync_id,
            adapter_type="google_ad_manager",
            sync_type=sync_type,
            triggered_by=triggered_by,
            triggered_by_id=triggered_by_id,
            started_at=started_at,
            progress={"phase": "starting", "date_range": date_range, "line_item_types": line_item_types},
        )
        session.commit()


def finish_catalog_sync_job(
    tenant_id: str,
    sync_id: str,
    succeeded: bool,
    counts: dict[str, int],
    errors: dict[str, str],
    summary: dict[str, int],
    progress: dict,
    finished_at: datetime,
) -> None:
    with get_db_session() as session:
        session.info["platform_background_worker"] = True
        repo = SyncJobRepository(session, tenant_id)
        if succeeded:
            repo.mark_completed(
                sync_id,
                summary=json.dumps(summary),
                progress=progress,
                completed_at=finished_at,
            )
        else:
            repo.mark_failed(
                sync_id,
                error_message=_sanitize_error_message(next(iter(errors.values()))),
                progress=progress,
                completed_at=finished_at,
            )
        session.commit()


def fail_catalog_sync_job(
    *,
    tenant_id: str,
    sync_id: str,
    exc: Exception,
    finished_at: datetime | None = None,
    item_count: int | None = None,
) -> tuple[datetime, str]:
    completed_at = finished_at or datetime.now(UTC)
    error_message = _sanitize_error_message(f"{type(exc).__name__}: {exc}")
    progress: dict = {"counts": {}, "errors": {"sync": error_message}}
    if item_count is not None:
        progress["item_count"] = item_count
    with get_db_session() as session:
        session.info["platform_background_worker"] = True
        SyncJobRepository(session, tenant_id).mark_failed(
            sync_id,
            error_message=error_message,
            progress=progress,
            completed_at=completed_at,
        )
        session.commit()
    return completed_at, error_message


async def dispatch_catalog_sync_tenant(
    *,
    tenant_id: str,
    sync_func: Callable[..., CatalogSyncResult],
    triggered_by: str,
    logger: logging.Logger,
    crash_message: str,
    failure_message: str,
) -> str | None:
    try:
        result = await asyncio.to_thread(sync_func, tenant_id=tenant_id, triggered_by=triggered_by)
    except Exception:
        logger.exception(crash_message, tenant_id)
        return None
    if not result.succeeded:
        logger.warning(failure_message, tenant_id, list(result.errors.keys()))
    return result.sync_id


async def run_catalog_sync_scheduler_once(
    eligible_tenant_ids: Iterable[str],
    *,
    max_concurrent: int,
    sync_func: Callable[..., CatalogSyncResult],
    triggered_by: str,
    logger: logging.Logger,
    crash_message: str,
    failure_message: str,
) -> list[str]:
    return await dispatch_limited(
        eligible_tenant_ids,
        max_concurrent=max_concurrent,
        dispatch=lambda tenant_id: dispatch_catalog_sync_tenant(
            tenant_id=tenant_id,
            sync_func=sync_func,
            triggered_by=triggered_by,
            logger=logger,
            crash_message=crash_message,
            failure_message=failure_message,
        ),
    )


async def run_catalog_sync_scheduler_cycle(
    *,
    now: datetime | None,
    list_eligible_tenants: Callable[[datetime], list[str]],
    max_concurrent: int,
    sync_func: Callable[..., CatalogSyncResult],
    triggered_by: str,
    logger: logging.Logger,
    crash_message: str,
    failure_message: str,
    cycle_complete_message: str,
) -> list[str]:
    snapshot = now or datetime.now(UTC)
    eligible = list_eligible_tenants(snapshot)
    if not eligible:
        return []
    dispatched = await run_catalog_sync_scheduler_once(
        eligible,
        max_concurrent=max_concurrent,
        sync_func=sync_func,
        triggered_by=triggered_by,
        logger=logger,
        crash_message=crash_message,
        failure_message=failure_message,
    )
    logger.info(cycle_complete_message, len(dispatched), len(eligible))
    return dispatched


async def dispatch_limited(
    items: Iterable[T],
    *,
    max_concurrent: int,
    dispatch: Callable[[T], Awaitable[str | None]],
) -> list[str]:
    semaphore = asyncio.Semaphore(max(1, max_concurrent))

    async def dispatch_one(item: T) -> str | None:
        async with semaphore:
            return await dispatch(item)

    return [sync_id for sync_id in await asyncio.gather(*(dispatch_one(item) for item in items)) if sync_id]
