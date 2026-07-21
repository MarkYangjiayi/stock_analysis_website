from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import event, text
import os
from core.config import settings

# ------------------------------------------------------------------------
# 异步数据库连接配置 (Async Database Connection Configuration)
# ------------------------------------------------------------------------

# 注意：目前切换为了 SQLite 驱动 sqlite+aiosqlite:///
DATABASE_URL = settings.DATABASE_URL

# 若为 SQLite 数据库，确保该文件夹存在以进行持久化映射
if DATABASE_URL.startswith("sqlite"):
    # extract path from sqlite+aiosqlite:///./data/quantify_local.db
    db_path = DATABASE_URL.split("///")[-1]
    if db_path.startswith("./"):
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

# 1. 创建异步引擎 (Engine)
# SQLite 支持 check_same_thread=False 来适应多线程的 FastAPI
engine_options = {"echo": False, "future": True, "pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False, "timeout": 30}

engine = create_async_engine(DATABASE_URL, **engine_options)

# 注册连接事件配置 SQLite 高性能 WAL 模式
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()

# 2. 创建异步 Session 工厂
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

# 3. 依赖注入函数: 提供给 FastAPI 路由使用获取 DB Session
async def get_db():
    async with async_session_maker() as session:
        yield session

# 4. 初始化数据库表: 供启动时自动建表
async def init_db():
    """
    启动时自动建表 (前期快速迭代时使用，不要用在生产环境的数据迁移中)。
    需要在此处引入所有 models 确保其被 SQLAlchemy registry 收集。
    """
    from models import Base
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def database_ready() -> bool:
    """Cheap readiness probe used by health checks and orchestration."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
