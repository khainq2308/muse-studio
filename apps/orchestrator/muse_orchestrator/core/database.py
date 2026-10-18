from __future__ import annotations

from collections.abc import AsyncGenerator
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import Base

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _ensure_schema_compatibility(sync_conn) -> None:
    """Add new columns to existing installations; create_all only creates tables."""
    inspector = inspect(sync_conn)
    if "generation_tasks" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("generation_tasks")}
    if "account_id_used" not in columns:
        sync_conn.execute(text(
            "ALTER TABLE generation_tasks ADD COLUMN account_id_used VARCHAR(64)"
        ))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_schema_compatibility)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
