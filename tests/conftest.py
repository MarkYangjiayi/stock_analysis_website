import os
import tempfile
from pathlib import Path

import pytest_asyncio


TEST_ROOT = Path(tempfile.mkdtemp(prefix="quantify-tests-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_ROOT / 'test.db'}"
os.environ["RAW_DATA_DIR"] = str(TEST_ROOT / "raw")
os.environ["BACKUP_DIR"] = str(TEST_ROOT / "backups")
os.environ["DATA_DIR"] = str(TEST_ROOT)
os.environ["ADMIN_API_KEY"] = "test-secret"
os.environ["ENVIRONMENT"] = "test"
os.environ["PIPELINE_MIN_UNIVERSE_SIZE"] = "2"
os.environ["PIPELINE_MIN_FUNDAMENTAL_COVERAGE"] = "0.80"


@pytest_asyncio.fixture(autouse=True)
async def clean_database():
    from database import engine
    from models import Base

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def db_session():
    from database import async_session_maker

    async with async_session_maker() as session:
        yield session
        await session.rollback()
