from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from muse_orchestrator.main import app


@pytest.mark.asyncio
async def test_cors_preflight_allows_studio_admin_header_and_supported_method() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "content-type,x-studio-admin-token",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    allowed_headers = {
        item.strip().lower()
        for item in response.headers["access-control-allow-headers"].split(",")
    }
    assert "content-type" in allowed_headers
    assert "x-studio-admin-token" in allowed_headers
    assert "patch" in response.headers["access-control-allow-methods"].lower()


@pytest.mark.asyncio
async def test_cors_preflight_rejects_unneeded_header_and_method() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        denied_header = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-unexpected-secret",
            },
        )
        denied_method = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "TRACE",
            },
        )

    assert denied_header.status_code == 400
    assert denied_method.status_code == 400


@pytest.mark.asyncio
async def test_cors_does_not_grant_other_origins_access() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.options(
            "/healthz",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
