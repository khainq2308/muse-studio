from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from muse_orchestrator.core.config import settings
from muse_orchestrator.models.domain import Base

config = context.config
target_metadata = Base.metadata

# Environment variables take precedence for CLI deployments. If the ini file still
# has its local default, honor settings loaded from .env as the API does.
_configured_url = os.environ.get("DATABASE_URL")
if not _configured_url:
    _configured_url = config.get_main_option("sqlalchemy.url")
    _default_url = "sqlite+aiosqlite:///./musestudio_dev.db"
    if _configured_url == _default_url:
        _configured_url = settings.database_url
config.set_main_option("sqlalchemy.url", _configured_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    # Alembic CLI is synchronous. Application startup invokes it in a worker thread,
    # so asyncio.run never competes with FastAPI's already-running event loop.
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
