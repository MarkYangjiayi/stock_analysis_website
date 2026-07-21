from datetime import date, datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session_maker
from models import DataPublication, PipelineRun
from core.time_utils import utc_now


async def begin_pipeline_run(name: str, target_date: Optional[date], version: str = "v2") -> int:
    async with async_session_maker() as db:
        run = PipelineRun(
            pipeline_name=name,
            target_date=target_date,
            status="running",
            stage="started",
            version=version,
        )
        db.add(run)
        await db.commit()
        await db.refresh(run)
        return run.id


async def update_pipeline_run(run_id: int, stage: str, records_processed: Optional[int] = None) -> None:
    async with async_session_maker() as db:
        run = await db.get(PipelineRun, run_id)
        if not run:
            return
        run.stage = stage
        if records_processed is not None:
            run.records_processed = records_processed
        await db.commit()


async def finish_pipeline_run(
    run_id: int,
    status: str,
    quality_report: Optional[dict] = None,
    error_message: Optional[str] = None,
) -> None:
    async with async_session_maker() as db:
        run = await db.get(PipelineRun, run_id)
        if not run:
            return
        run.status = status
        run.stage = status
        run.finished_at = utc_now()
        run.quality_report = quality_report
        run.error_message = error_message
        await db.commit()


async def publish_dataset(
    dataset: str,
    as_of_date: date,
    run_id: int,
    *,
    db: Optional[AsyncSession] = None,
    published_at: Optional[datetime] = None,
) -> None:
    """Publish a dataset, optionally inside the caller's data transaction."""
    timestamp = published_at or utc_now()
    stmt = insert(DataPublication).values(
        dataset=dataset,
        as_of_date=as_of_date,
        pipeline_run_id=run_id,
        status="published",
        published_at=timestamp,
    ).on_conflict_do_update(
        index_elements=["dataset", "as_of_date"],
        set_={
            "pipeline_run_id": run_id,
            "status": "published",
            "published_at": timestamp,
        },
    )
    if db is not None:
        await db.execute(stmt)
        return
    async with async_session_maker() as owned_db:
        await owned_db.execute(stmt)
        await owned_db.commit()


async def publish_datasets_and_finish(
    db: AsyncSession,
    datasets: Iterable[str],
    as_of_date: date,
    run_id: int,
    *,
    quality_report: Optional[dict] = None,
    records_processed: Optional[int] = None,
) -> datetime:
    """Atomically publish datasets and mark their source run successful.

    The caller owns the transaction and must commit it together with the
    normalized rows that the publications expose.
    """
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise ValueError(f"Pipeline run {run_id} does not exist")
    timestamp = utc_now()
    for dataset in datasets:
        await publish_dataset(
            dataset,
            as_of_date,
            run_id,
            db=db,
            published_at=timestamp,
        )
    run.status = "published"
    run.stage = "published"
    run.finished_at = timestamp
    run.quality_report = quality_report
    run.error_message = None
    if records_processed is not None:
        run.records_processed = records_processed
    return timestamp


async def latest_published_date(dataset: str) -> Optional[date]:
    async with async_session_maker() as db:
        result = await db.execute(
            select(DataPublication.as_of_date)
            .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
            .where(DataPublication.dataset == dataset, DataPublication.status == "published")
            .where(PipelineRun.status == "published")
            .order_by(DataPublication.as_of_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
