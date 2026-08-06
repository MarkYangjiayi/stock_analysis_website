from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from api.routers import router as api_router
import logging
from core.config import settings
from services.anomaly_scans import (
    recover_interrupted_anomaly_scans,
    shutdown_anomaly_scan_tasks,
)
from services.filing_analysis import (
    recover_interrupted_filing_analyses,
    shutdown_filing_analysis_tasks,
    start_filing_analysis_recovery_monitor,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行
    logger.info("Initializing application and database")
    if settings.ENVIRONMENT.lower() == "production" and not settings.ADMIN_API_KEY:
        logger.warning(
            "ADMIN_API_KEY is not configured; public APIs remain available "
            "but admin operations are disabled"
        )
    await init_db()
    await recover_interrupted_anomaly_scans()
    await recover_interrupted_filing_analyses()
    start_filing_analysis_recovery_monitor()
    yield
    await shutdown_filing_analysis_tasks()
    await shutdown_anomaly_scan_tasks()
    logger.info("Shutting down application")

# 初始化 FastAPI 实例，挂载 lifespan 生命周期
app = FastAPI(
    title="Stock Analysis Platform API",
    description="A powerful backend API for fetching and querying quantitative stock data.",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

# 配置 CORS 中间件，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册所有业务路由
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    # 为了方便本地快速测试可直接运行 python main.py
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
