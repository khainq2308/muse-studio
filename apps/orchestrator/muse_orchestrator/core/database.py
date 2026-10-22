from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from muse_orchestrator.core.config import settings


@event.listens_for(Engine, "connect")
def _enforce_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
    """SQLite ignores FOREIGN KEY clauses unless enabled per connection.

    PostgreSQL always enforces them, so dev/test SQLite must too; otherwise insert
    ordering and ON DELETE CASCADE bugs only surface in production.
    """
    module = type(dbapi_connection).__module__
    if "sqlite" in module:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


def _run_alembic_upgrade(database_url: str | None = None) -> None:
    """Apply versioned schema migrations using a synchronous Alembic command runner.

    Alembic's environment creates its own async SQLAlchemy engine. Run the command
    in a worker thread from the application so its event loop remains unblocked.
    The optional URL is primarily used by isolated migration tests.
    """
    from alembic import command
    from alembic.config import Config

    config_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    alembic_config = Config(str(config_path))
    target_url = database_url or settings.database_url
    alembic_config.set_main_option("sqlalchemy.url", target_url.replace("%", "%%"))
    command.upgrade(alembic_config, "head")


async def init_db() -> None:
    """Bring both new and pre-Alembic databases to the latest migration revision."""
    await asyncio.to_thread(_run_alembic_upgrade)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
