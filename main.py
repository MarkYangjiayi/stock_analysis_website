from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import init_db
from api.routers import router as api_router
from services.notifications import NotificationManager
from core.scheduler import start_scheduler, shutdown_scheduler
from services.ws_monitor import ws_monitor
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时执行
    print("Initializing application and database...")
    await init_db()
    
    # Trigger startup notification
    await NotificationManager.broadcast(
        title="System Status",
        content="Quantify Platform API Server Started. 🚀",
        channels=["feishu"]
    )
    
    # Start the Daily Reporter APScheduler
    start_scheduler()
    
    # Start the WebSocket Monitor background task
    monitor_task = asyncio.create_task(ws_monitor.start())
    
    yield
    
    # 关闭时执行 (如有需要释放的资源可以放这里)
    print("Shutting down application...")
    shutdown_scheduler()
    monitor_task.cancel()

# 初始化 FastAPI 实例，挂载 lifespan 生命周期
app = FastAPI(
    title="Stock Analysis Platform API",
    description="A powerful backend API for fetching and querying quantitative stock data.",
    version="1.0.0",
    lifespan=lifespan
)

# 配置 CORS 中间件，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册所有业务路由
app.include_router(api_router)


if __name__ == "__main__":
    import uvicorn
    # 为了方便本地快速测试可直接运行 python main.py
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
