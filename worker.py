"""Dedicated background worker process.

Run separately from Uvicorn so web replicas never duplicate scheduled jobs.
"""

import asyncio
import logging
import signal

from core.config import settings
from core.scheduler import shutdown_scheduler, start_scheduler
from database import init_db
from services.notifications import NotificationManager
from services.ws_monitor import ws_monitor
from services.catchup import catch_up_latest_publications
from services.anomaly_scans import recover_interrupted_anomaly_scans
from services.rsi_monitor import run_daily_rsi_monitor


logger = logging.getLogger(__name__)


async def run_worker() -> None:
    await init_db()
    await recover_interrupted_anomaly_scans()
    try:
        await catch_up_latest_publications()
    except Exception as exc:
        logger.exception("Startup catch-up failed; scheduled jobs remain active: %s", exc)
    if settings.RSI_MONITOR_ENABLED:
        try:
            await run_daily_rsi_monitor()
        except Exception as exc:
            logger.exception("Startup RSI monitor catch-up failed; scheduled job remains active: %s", exc)
    start_scheduler()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    monitor_task = None
    if settings.ENABLE_WS_MONITOR:
        monitor_task = asyncio.create_task(ws_monitor.start())

    await NotificationManager.broadcast("System Status", "Quantify worker started. 🚀")
    try:
        await stop_event.wait()
    finally:
        shutdown_scheduler()
        if monitor_task:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
