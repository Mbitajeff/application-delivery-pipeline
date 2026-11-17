"""Tests for bearer-token protection on the Prometheus /metrics endpoint."""

import pytest
from fastapi.testclient import TestClient

TEST_METRICS_TOKEN = "test-metrics-token-not-a-real-secret"


@pytest.fixture
def metrics_token(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure METRICS_TOKEN. get_settings() is read per request, so this applies immediately."""
    from app.core.config import get_settings

    monkeypatch.setenv("METRICS_TOKEN", TEST_METRICS_TOKEN)
    get_settings.cache_clear()
    return TEST_METRICS_TOKEN


class TestMetricsAuth:
    def test_no_token_configured_rejects_everything(self, client: TestClient) -> None:
        response = client.get("/metrics", headers={"Authorization": "Bearer anything"})
        assert response.status_code == 401

    def test_missing_header_rejected(self, client: TestClient, metrics_token: str) -> None:
        response = client.get("/metrics")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_wrong_token_rejected(self, client: TestClient, metrics_token: str) -> None:
        response = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401

    def test_basic_auth_rejected(self, client: TestClient, metrics_token: str) -> None:
        response = client.get("/metrics", auth=("grafana", metrics_token))
        assert response.status_code == 401

    def test_valid_token_returns_metrics(self, client: TestClient, metrics_token: str) -> None:
        response = client.get("/metrics", headers={"Authorization": f"Bearer {metrics_token}"})
        assert response.status_code == 200
        assert "http_requests_total" in response.text or "python_gc" in response.text

    def test_metrics_hidden_from_openapi(self, client: TestClient) -> None:
        assert "/metrics" not in client.get("/openapi.json").json()["paths"]
