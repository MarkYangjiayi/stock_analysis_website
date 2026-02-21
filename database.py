from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from core.config import settings

# ------------------------------------------------------------------------
# 异步数据库连接配置 (Async Database Connection Configuration)
# ------------------------------------------------------------------------

# 注意：使用 asyncpg 驱动，协议为 postgresql+asyncpg://
DATABASE_URL = settings.DATABASE_URL

# 1. 创建异步引擎 (Engine)
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    pool_size=5,
    max_overflow=10,
)

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
