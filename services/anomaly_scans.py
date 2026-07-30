import asyncio
import logging
import time
import uuid
from datetime import timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from database import async_session_maker
from models import AnomalyScanRun
from services.anomaly_detector import (
    AnomalyScanData,
    AnomalyScanError,
    scan_and_analyze_anomalies,
)


logger = logging.getLogger(__name__)
ACTIVE_SCAN_STATUSES = ("queued", "running")
SCHEDULED_SCAN_TRIGGERS = ("morning_briefing", "post_market_summary")
_PROCESS_OWNER_TOKEN = uuid.uuid4().hex
_enqueue_lock = asyncio.Lock()
_running_tasks: Dict[int, asyncio.Task[None]] = {}


def _iso_utc(value) -> Optional[str]:
    if value is None:
        return None
    normalized = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return normalized.isoformat().replace("+00:00", "Z")


def serialize_anomaly_scan(scan: AnomalyScanRun) -> Dict[str, Any]:
    return {
        "id": scan.id,
        "trigger": scan.trigger,
        "status": scan.status,
        "requested_limit": scan.requested_limit,
        "threshold_pct": scan.threshold_pct,
        "universe_as_of": (
            scan.universe_as_of.isoformat()
            if scan.universe_as_of
            else None
        ),
        "quote_as_of": _iso_utc(scan.quote_as_of),
        "results": scan.results or [],
        "error_message": scan.error_message,
        "created_at": _iso_utc(scan.created_at),
        "started_at": _iso_utc(scan.started_at),
        "finished_at": _iso_utc(scan.finished_at),
    }


async def get_anomaly_scan(
    db: AsyncSession,
    scan_id: int,
) -> Optional[AnomalyScanRun]:
    await _expire_stale_scans(db)
    await db.commit()
    return await db.get(AnomalyScanRun, scan_id)


async def get_latest_completed_anomaly_scan(
    db: AsyncSession,
) -> Optional[AnomalyScanRun]:
    result = await db.execute(
        select(AnomalyScanRun)
        .where(AnomalyScanRun.status == "completed")
        .order_by(
            AnomalyScanRun.finished_at.desc(),
            AnomalyScanRun.id.desc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _expire_stale_scans(db: AsyncSession) -> None:
    now = utc_now()
    await db.execute(
        update(AnomalyScanRun)
        .where(
            AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
            or_(
                AnomalyScanRun.lease_expires_at.is_(None),
                AnomalyScanRun.lease_expires_at <= now,
            ),
        )
        .values(
            status="failed",
            active_key=None,
            owner_token=None,
            lease_expires_at=None,
            error_message="Scan ownership lease expired before completion",
            finished_at=now,
        )
    )


async def recover_interrupted_anomaly_scans() -> None:
    """Release only scans whose owning process lease has expired."""
    async with async_session_maker() as db:
        await _expire_stale_scans(db)
        await db.commit()


def _lease_deadline():
    return utc_now() + timedelta(
        seconds=max(1.0, settings.ANOMALY_SCAN_LEASE_SECONDS)
    )


async def enqueue_manual_anomaly_scan(
    db: AsyncSession,
) -> Tuple[AnomalyScanRun, bool]:
    """Return an active single-flight run or create a new queued run."""
    async with _enqueue_lock:
        await _expire_stale_scans(db)
        existing_result = await db.execute(
            select(AnomalyScanRun)
            .where(AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES))
            .order_by(AnomalyScanRun.id.desc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            await db.commit()
            return existing, False

        scan = AnomalyScanRun(
            trigger="manual",
            status="queued",
            active_key="market",
            owner_token=_PROCESS_OWNER_TOKEN,
            lease_expires_at=_lease_deadline(),
            requested_limit=settings.ANOMALY_RESULT_LIMIT,
            threshold_pct=settings.ANOMALY_MOVE_THRESHOLD_PCT,
            results=[],
        )
        db.add(scan)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            concurrent_result = await db.execute(
                select(AnomalyScanRun)
                .where(AnomalyScanRun.active_key == "market")
                .limit(1)
            )
            concurrent = concurrent_result.scalar_one_or_none()
            if concurrent is None:
                latest_result = await db.execute(
                    select(AnomalyScanRun)
                    .order_by(AnomalyScanRun.id.desc())
                    .limit(1)
                )
                concurrent = latest_result.scalar_one()
            return concurrent, False
        await db.refresh(scan)
        return scan, True


def _scan_satisfies_request(
    scan: AnomalyScanRun,
    *,
    trigger: str,
    limit_count: int,
    threshold_pct: float,
) -> bool:
    requires_current_session = trigger in SCHEDULED_SCAN_TRIGGERS
    existing_requires_current_session = (
        scan.trigger in SCHEDULED_SCAN_TRIGGERS
    )
    return (
        scan.requested_limit >= limit_count
        and scan.threshold_pct == threshold_pct
        and (
            not requires_current_session
            or existing_requires_current_session
        )
    )


async def _create_scan_run(
    trigger: str,
    limit_count: int,
) -> Tuple[int, bool, bool]:
    normalized_limit = max(1, min(limit_count, 10))
    threshold_pct = settings.ANOMALY_MOVE_THRESHOLD_PCT
    async with async_session_maker() as db:
        await _expire_stale_scans(db)
        existing_result = await db.execute(
            select(AnomalyScanRun)
            .where(AnomalyScanRun.active_key == "market")
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            await db.commit()
            return (
                existing.id,
                False,
                _scan_satisfies_request(
                    existing,
                    trigger=trigger,
                    limit_count=normalized_limit,
                    threshold_pct=threshold_pct,
                ),
            )

        scan = AnomalyScanRun(
            trigger=trigger,
            status="queued",
            active_key="market",
            owner_token=_PROCESS_OWNER_TOKEN,
            lease_expires_at=_lease_deadline(),
            requested_limit=normalized_limit,
            threshold_pct=threshold_pct,
            results=[],
        )
        db.add(scan)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            concurrent_result = await db.execute(
                select(AnomalyScanRun)
                .where(AnomalyScanRun.active_key == "market")
                .limit(1)
            )
            concurrent = concurrent_result.scalar_one_or_none()
            if concurrent is None:
                latest_result = await db.execute(
                    select(AnomalyScanRun)
                    .order_by(AnomalyScanRun.id.desc())
                    .limit(1)
                )
                concurrent = latest_result.scalar_one()
            return (
                concurrent.id,
                False,
                _scan_satisfies_request(
                    concurrent,
                    trigger=trigger,
                    limit_count=normalized_limit,
                    threshold_pct=threshold_pct,
                ),
            )
        await db.refresh(scan)
        return scan.id, True, True


async def _claim_anomaly_scan(
    scan_id: int,
) -> Optional[Tuple[int, float, str]]:
    """Atomically claim an unowned, expired, or already-owned scan."""
    now = utc_now()
    async with async_session_maker() as db:
        result = await db.execute(
            update(AnomalyScanRun)
            .where(
                AnomalyScanRun.id == scan_id,
                AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
                or_(
                    AnomalyScanRun.owner_token == _PROCESS_OWNER_TOKEN,
                    AnomalyScanRun.owner_token.is_(None),
                    AnomalyScanRun.lease_expires_at.is_(None),
                    AnomalyScanRun.lease_expires_at <= now,
                ),
            )
            .values(
                status="running",
                owner_token=_PROCESS_OWNER_TOKEN,
                lease_expires_at=_lease_deadline(),
                started_at=now,
                error_message=None,
            )
        )
        await db.commit()
        if result.rowcount != 1:
            return None
        scan = await db.get(AnomalyScanRun, scan_id)
        if scan is None:
            return None
        return (
            scan.requested_limit,
            scan.threshold_pct,
            scan.trigger,
        )


async def _renew_scan_lease(scan_id: int) -> None:
    lease_seconds = max(1.0, settings.ANOMALY_SCAN_LEASE_SECONDS)
    interval = max(0.25, lease_seconds / 3)
    while True:
        await asyncio.sleep(interval)
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    update(AnomalyScanRun)
                    .where(
                        AnomalyScanRun.id == scan_id,
                        AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
                        AnomalyScanRun.owner_token == _PROCESS_OWNER_TOKEN,
                    )
                    .values(lease_expires_at=_lease_deadline())
                )
                await db.commit()
                if result.rowcount != 1:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Failed to renew ownership lease for anomaly scan %s",
                scan_id,
            )
            return


async def _record_scan_completion(
    scan_id: int,
    data: AnomalyScanData,
) -> None:
    async with async_session_maker() as db:
        result = await db.execute(
            update(AnomalyScanRun)
            .where(
                AnomalyScanRun.id == scan_id,
                AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
                AnomalyScanRun.owner_token == _PROCESS_OWNER_TOKEN,
            )
            .values(
                status="completed",
                active_key=None,
                owner_token=None,
                lease_expires_at=None,
                universe_as_of=data.universe_as_of,
                quote_as_of=data.quote_as_of.astimezone(
                    timezone.utc
                ).replace(tzinfo=None),
                results=data.results,
                error_message=None,
                finished_at=utc_now(),
            )
        )
        await db.commit()
        if result.rowcount != 1:
            logger.warning(
                "Ignored completion from a process that no longer owns anomaly scan %s",
                scan_id,
            )


async def execute_anomaly_scan(scan_id: int) -> None:
    """Execute one durable scan while holding a renewable process lease."""
    heartbeat_task: Optional[asyncio.Task[None]] = None
    try:
        claimed = await _claim_anomaly_scan(scan_id)
        if claimed is None:
            return
        requested_limit, threshold_pct, trigger = claimed
        heartbeat_task = asyncio.create_task(_renew_scan_lease(scan_id))

        async def run_detection():
            async with async_session_maker() as db:
                return await scan_and_analyze_anomalies(
                    db,
                    limit_count=requested_limit,
                    threshold_pct=threshold_pct,
                    require_current_session=(
                        trigger in SCHEDULED_SCAN_TRIGGERS
                    ),
                )

        data = await asyncio.wait_for(
            run_detection(),
            timeout=settings.ANOMALY_SCAN_TIMEOUT_SECONDS,
        )
        await _record_scan_completion(scan_id, data)
    except asyncio.TimeoutError:
        logger.warning("Anomaly scan %s exceeded its total time budget", scan_id)
        await _record_scan_failure(scan_id, "Scan timed out before completion")
    except AnomalyScanError as exc:
        logger.warning("Anomaly scan %s failed: %s", scan_id, exc)
        await _record_scan_failure(scan_id, str(exc))
    except Exception:
        logger.exception("Unexpected failure in anomaly scan %s", scan_id)
        await _record_scan_failure(
            scan_id,
            "An unexpected error prevented the scan from completing",
        )
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)


async def _record_scan_failure(scan_id: int, message: str) -> None:
    async with async_session_maker() as db:
        await db.execute(
            update(AnomalyScanRun)
            .where(
                AnomalyScanRun.id == scan_id,
                AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
                AnomalyScanRun.owner_token == _PROCESS_OWNER_TOKEN,
            )
            .values(
                status="failed",
                active_key=None,
                owner_token=None,
                lease_expires_at=None,
                error_message=message,
                finished_at=utc_now(),
            )
        )
        await db.commit()


def schedule_anomaly_scan(scan_id: int) -> None:
    existing = _running_tasks.get(scan_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(execute_anomaly_scan(scan_id))
    _running_tasks[scan_id] = task

    def discard(completed: asyncio.Task[None]) -> None:
        _running_tasks.pop(scan_id, None)
        if completed.cancelled():
            return
        try:
            completed.result()
        except Exception:
            logger.exception("Anomaly scan task %s escaped its failure handler", scan_id)

    task.add_done_callback(discard)


async def shutdown_anomaly_scan_tasks() -> None:
    """Cancel web-process tasks and make their durable state explicit."""
    pending = [
        (scan_id, task)
        for scan_id, task in _running_tasks.items()
        if not task.done()
    ]
    if not pending:
        return
    for _, task in pending:
        task.cancel()
    await asyncio.gather(
        *(task for _, task in pending),
        return_exceptions=True,
    )
    for scan_id, task in pending:
        if task.cancelled():
            await _record_scan_failure(
                scan_id,
                "Scan was interrupted by a service shutdown",
            )


async def run_persisted_anomaly_scan(
    *,
    trigger: str,
    limit_count: int,
) -> list[dict]:
    """Run and persist a scheduled scan, returning results to the reporter."""
    normalized_limit = max(1, min(limit_count, 10))
    deadline = time.monotonic() + (
        (settings.ANOMALY_SCAN_TIMEOUT_SECONDS + 30) * 2
    )

    while True:
        scan_id, created, compatible = await _create_scan_run(
            trigger,
            normalized_limit,
        )
        if created:
            await execute_anomaly_scan(scan_id)
            break

        terminal_status: Optional[str] = None
        while time.monotonic() < deadline:
            async with async_session_maker() as db:
                await _expire_stale_scans(db)
                await db.commit()
                active = await db.get(AnomalyScanRun, scan_id)
                terminal_status = active.status if active else None
                if terminal_status not in ACTIVE_SCAN_STATUSES:
                    break
            await asyncio.sleep(0.5)
        else:
            raise AnomalyScanError(
                "Timed out waiting for the active anomaly scan"
            )

        if compatible and terminal_status == "completed":
            break
        # Failed runs and parameter-incompatible results cannot satisfy this
        # caller. Once their active key is released, loop and create a new run.

    async with async_session_maker() as db:
        scan = await db.get(AnomalyScanRun, scan_id)
        if scan is None or scan.status != "completed":
            message = (
                scan.error_message
                if scan and scan.error_message
                else "Anomaly scan did not complete"
            )
            raise AnomalyScanError(message)
        return (scan.results or [])[:normalized_limit]
