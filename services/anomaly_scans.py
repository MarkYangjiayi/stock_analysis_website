import asyncio
import logging
import time
from datetime import timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from database import async_session_maker
from models import AnomalyScanRun
from services.anomaly_detector import (
    AnomalyScanError,
    scan_and_analyze_anomalies,
)


logger = logging.getLogger(__name__)
ACTIVE_SCAN_STATUSES = ("queued", "running")
EXECUTOR_SCAN_TRIGGERS = {
    "web": ("manual",),
    "worker": ("morning_briefing", "post_market_summary"),
}
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
    cutoff = utc_now() - timedelta(
        seconds=settings.ANOMALY_SCAN_TIMEOUT_SECONDS + 30
    )
    await db.execute(
        update(AnomalyScanRun)
        .where(
            AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
            AnomalyScanRun.created_at < cutoff,
        )
        .values(
            status="failed",
            active_key=None,
            error_message="Scan was interrupted before it completed",
            finished_at=utc_now(),
        )
    )


async def recover_interrupted_anomaly_scans(
    executor_role: str = "web",
) -> None:
    """Fail orphaned runs owned by the executor process that is starting."""
    owned_triggers = EXECUTOR_SCAN_TRIGGERS.get(executor_role)
    if owned_triggers is None:
        raise ValueError(f"Unknown anomaly scan executor role: {executor_role}")

    async with async_session_maker() as db:
        finished_at = utc_now()
        result = await db.execute(
            update(AnomalyScanRun)
            .where(
                AnomalyScanRun.status.in_(ACTIVE_SCAN_STATUSES),
                AnomalyScanRun.trigger.in_(owned_triggers),
            )
            .values(
                status="failed",
                active_key=None,
                error_message="Scan was interrupted before it completed",
                finished_at=finished_at,
            )
        )
        await db.commit()
        if result.rowcount:
            logger.warning(
                "Marked %s interrupted %s anomaly scan(s) as failed during startup",
                result.rowcount,
                executor_role,
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
    limit_count: int,
    threshold_pct: float,
) -> bool:
    return (
        scan.requested_limit >= limit_count
        and scan.threshold_pct == threshold_pct
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
                    limit_count=normalized_limit,
                    threshold_pct=threshold_pct,
                ),
            )

        scan = AnomalyScanRun(
            trigger=trigger,
            status="queued",
            active_key="market",
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
                    limit_count=normalized_limit,
                    threshold_pct=threshold_pct,
                ),
            )
        await db.refresh(scan)
        return scan.id, True, True


async def execute_anomaly_scan(scan_id: int) -> None:
    """Execute one durable scan, recording either a completed or failed state."""
    try:
        async with async_session_maker() as db:
            scan = await db.get(AnomalyScanRun, scan_id)
            if scan is None or scan.status not in ACTIVE_SCAN_STATUSES:
                return
            scan.status = "running"
            scan.started_at = utc_now()
            scan.error_message = None
            await db.commit()
            requested_limit = scan.requested_limit
            threshold_pct = scan.threshold_pct

        async def run_detection():
            async with async_session_maker() as db:
                return await scan_and_analyze_anomalies(
                    db,
                    limit_count=requested_limit,
                    threshold_pct=threshold_pct,
                )

        data = await asyncio.wait_for(
            run_detection(),
            timeout=settings.ANOMALY_SCAN_TIMEOUT_SECONDS,
        )

        async with async_session_maker() as db:
            scan = await db.get(AnomalyScanRun, scan_id)
            if scan is None:
                return
            scan.status = "completed"
            scan.active_key = None
            scan.universe_as_of = data.universe_as_of
            scan.quote_as_of = data.quote_as_of.astimezone(
                timezone.utc
            ).replace(tzinfo=None)
            scan.results = data.results
            scan.finished_at = utc_now()
            await db.commit()
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


async def _record_scan_failure(scan_id: int, message: str) -> None:
    async with async_session_maker() as db:
        scan = await db.get(AnomalyScanRun, scan_id)
        if scan is None:
            return
        scan.status = "failed"
        scan.active_key = None
        scan.error_message = message
        scan.finished_at = utc_now()
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

        while time.monotonic() < deadline:
            async with async_session_maker() as db:
                active = await db.get(AnomalyScanRun, scan_id)
                if active is None or active.status not in ACTIVE_SCAN_STATUSES:
                    break
            await asyncio.sleep(0.5)
        else:
            raise AnomalyScanError(
                "Timed out waiting for the active anomaly scan"
            )

        if compatible:
            break
        # The completed run requested fewer results (or a different threshold)
        # and cannot satisfy this caller. Once its active key is released, loop
        # and create the requested scheduled scan.

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
