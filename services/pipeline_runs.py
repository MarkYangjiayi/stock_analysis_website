from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

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


async def publish_dataset(dataset: str, as_of_date: date, run_id: int) -> None:
    async with async_session_maker() as db:
        stmt = insert(DataPublication).values(
            dataset=dataset,
            as_of_date=as_of_date,
            pipeline_run_id=run_id,
            status="published",
            published_at=utc_now(),
        ).on_conflict_do_update(
            index_elements=["dataset", "as_of_date"],
            set_={
                "pipeline_run_id": run_id,
                "status": "published",
                "published_at": utc_now(),
            },
        )
        await db.execute(stmt)
        await db.commit()


async def latest_published_date(dataset: str) -> Optional[date]:
    async with async_session_maker() as db:
        result = await db.execute(
            select(DataPublication.as_of_date)
            .where(DataPublication.dataset == dataset, DataPublication.status == "published")
            .order_by(DataPublication.as_of_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
