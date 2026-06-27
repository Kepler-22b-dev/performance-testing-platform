"""
数据库模块 - PostgreSQL 连接管理
提供同步和异步 SQLAlchemy 引擎
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://gungun@localhost:5432/perftest"
)

# 异步引擎（FastAPI 路由使用）
engine = create_async_engine(
    DATABASE_URL, echo=False,
    pool_size=10, max_overflow=20,
    pool_timeout=30, pool_recycle=1800, pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# 同步引擎（调度器等同步代码使用）
SYNC_DATABASE_URL = DATABASE_URL.replace("+asyncpg", "")
sync_engine = create_engine(
    SYNC_DATABASE_URL, echo=False,
    pool_size=5, max_overflow=10,
    pool_timeout=30, pool_recycle=1800, pool_pre_ping=True,
)
SyncSession = sessionmaker(bind=sync_engine)


class Base(DeclarativeBase):
    pass


def get_sync_db():
    """获取同步数据库会话"""
    return SyncSession()


async def get_db():
    """FastAPI 依赖注入：获取异步数据库会话"""
    async with async_session() as session:
        yield session


async def init_db():
    """初始化数据库：创建所有表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
