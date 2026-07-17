"""
数据库模块 - PostgreSQL 连接管理
提供同步和异步 SQLAlchemy 引擎
"""
import asyncio
from pathlib import Path
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from common.config import DATABASE_URL

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
    """通过 Alembic 将数据库升级到最新版本。"""
    await asyncio.to_thread(run_migrations)


def run_migrations() -> None:
    """运行 Alembic 迁移，避免应用启动时绕过数据库版本管理。"""
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parent.parent
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", SYNC_DATABASE_URL.replace("%", "%%"))
    tables = set(inspect(sync_engine).get_table_names())
    core_tables = {"scripts", "tasks", "task_results"}
    if core_tables.issubset(tables) and "alembic_version" not in tables:
        # 兼容旧版本由 SQLAlchemy create_all() 创建、但没有 Alembic 版本记录的数据库。
        command.stamp(config, "8aea793400de")
    elif tables.intersection(core_tables) and not core_tables.issubset(tables):
        raise RuntimeError("检测到不完整的旧数据库结构，请先备份并人工确认迁移")
    command.upgrade(config, "head")
