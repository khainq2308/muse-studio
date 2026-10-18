# tests/conftest.py (consolidated fixture)

```python
from __future__ import annotations

import os
import tempfile

# Must run before any muse_orchestrator import.
_TEMP_MEDIA_DIR = tempfile.mkdtemp(prefix="muse_test_")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["MEDIA_ROOT"] = _TEMP_MEDIA_DIR
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from muse_orchestrator.core.database import get_db
from muse_orchestrator.main import app
from muse_orchestrator.models.domain import Base


@pytest.fixture()
async def session_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture()
async def client(session_factory):
    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
```

Migration steps: add this file, remove the duplicated `client` fixtures and their env-var preambles from each test module (keep module-specific fixtures), run the full suite, confirm the test count is unchanged.
