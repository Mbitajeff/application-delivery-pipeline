"""
Integration tests for health endpoints.

These tests use the full HTTP stack via FastAPI TestClient.
"""


class TestHealthEndpoints:
    """Tests for /health and /readiness."""

    def test_health_returns_200(self, client) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client) -> None:
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "version" in data
        assert "environment" in data

    def test_health_environment_is_testing(self, client) -> None:
        response = client.get("/health")
        data = response.json()
        assert data["environment"] == "testing"

    def test_readiness_returns_200(self, client) -> None:
        response = client.get("/readiness")
        assert response.status_code == 200

    def test_readiness_response_structure(self, client) -> None:
        response = client.get("/readiness")
        data = response.json()
        assert data["status"] == "ready"
        assert "checks" in data
        assert data["checks"]["database"] == "ok"
