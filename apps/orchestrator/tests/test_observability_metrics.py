from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from muse_orchestrator.core import security
from muse_orchestrator.main import app
from muse_orchestrator.metrics import RequestMetrics, request_metrics


@pytest.mark.asyncio
async def test_metrics_endpoint_is_authenticated_and_healthz_stays_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_metrics.clear()
    monkeypatch.setattr(security.settings, "studio_admin_token", "metrics-admin-token")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        denied = await client.get("/metrics")
        assert denied.status_code == 401

        health = await client.get("/healthz")
        assert health.status_code == 200

        scraped = await client.get(
            "/metrics",
            headers={"X-Studio-Admin-Token": "metrics-admin-token"},
        )
        assert scraped.status_code == 200
        assert scraped.headers["content-type"].startswith("text/plain")
        assert "# TYPE muse_http_requests_total counter" in scraped.text
        assert "# TYPE muse_http_request_duration_seconds histogram" in scraped.text
        assert 'method="GET",route="/healthz",status_code="200"' in scraped.text
        assert 'method="GET",route="/metrics",status_code="401"' in scraped.text


@pytest.mark.asyncio
async def test_metrics_use_route_templates_instead_of_raw_resource_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_metrics.clear()
    monkeypatch.setattr(security.settings, "studio_admin_token", "metrics-admin-token")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/api/v1/projects/private-project-id/timeline")
        assert response.status_code == 401

        scraped = await client.get(
            "/metrics",
            headers={"X-Studio-Admin-Token": "metrics-admin-token"},
        )
        assert scraped.status_code == 200
        assert 'route="/api/v1/projects/{project_id}/timeline"' in scraped.text
        assert "private-project-id" not in scraped.text


def test_request_metrics_histogram_buckets_are_cumulative() -> None:
    metrics = RequestMetrics()
    metrics.observe(
        method="get",
        route="/test/{item_id}",
        status_code=204,
        duration_seconds=0.03,
    )

    rendered = metrics.render_prometheus()
    assert 'method="GET",route="/test/{item_id}",le="0.025"' in rendered
    assert 'method="GET",route="/test/{item_id}",le="0.05"} 1' in rendered
    assert 'method="GET",route="/test/{item_id}",le="+Inf"} 1' in rendered
    assert 'method="GET",route="/test/{item_id}"} 1' in rendered
