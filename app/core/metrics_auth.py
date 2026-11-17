"""
Bearer-token protection for the Prometheus /metrics endpoint.

/metrics exposes route names, request volumes and runtime details, so it
must not be publicly readable. Scrapers (e.g. a Grafana Cloud Metrics
Endpoint scrape job) send `Authorization: Bearer <METRICS_TOKEN>`.

Fail-closed: if METRICS_TOKEN is not configured, every request is rejected.
"""

import secrets

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


def require_metrics_token(request: Request) -> None:
    """FastAPI dependency: reject the request unless it carries the metrics token."""
    configured = get_settings().metrics_token
    expected = configured.get_secret_value() if configured else ""

    scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")

    # compare_digest avoids leaking the token through response timing
    if (
        not expected
        or scheme.lower() != "bearer"
        or not secrets.compare_digest(supplied.strip().encode(), expected.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
