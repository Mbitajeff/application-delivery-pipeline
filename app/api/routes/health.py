"""
Health and readiness endpoints.

Two distinct endpoints — a common pattern in container orchestration:

/health   — liveness probe: is the process alive and not deadlocked?
            Returns 200 if the app is running at all.
            Kubernetes restarts the container if this fails.

/readiness — readiness probe: is the app ready to serve traffic?
             Returns 200 when all dependencies are reachable.
             Kubernetes removes the pod from load balancer rotation if
             this fails (without restarting it).

Keeping these separate prevents Kubernetes from restarting healthy pods
that are temporarily unable to serve traffic (e.g. during DB migration).
"""

from fastapi import APIRouter

from app.core.config import get_settings
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get(
    "/health",
    summary="Liveness check",
    description="Returns 200 if the application process is alive.",
    response_description="Application is alive",
)
async def health() -> dict:
    """Liveness probe — always returns OK if the process is running."""
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
    }


@router.get(
    "/readiness",
    summary="Readiness check",
    description="Returns 200 if the application is ready to serve traffic.",
    response_description="Application is ready",
)
async def readiness() -> dict:
    """
    Readiness probe.

    In this implementation the check is trivial because we have no
    external dependencies. In a real application this would check:
    - Database connectivity
    - Cache reachability
    - Required external service availability
    """
    logger.debug("readiness_check")
    return {
        "status": "ready",
        "checks": {
            "database": "ok",   # In-memory store is always available
        },
    }
