"""
FastAPI application entrypoint.

Application structure:
- Lifespan context manager handles startup/shutdown
- CORS middleware configured for development origins
- Routes registered under versioned prefix /api/v1
- Global exception handlers for consistent error format
- OpenAPI docs available at /docs (disabled in production)
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import REGISTRY
from prometheus_client.exposition import choose_encoder

# Prometheus metrics — exposes /metrics endpoint for Grafana/Prometheus scraping
from prometheus_fastapi_instrumentator import Instrumentator

from app.api.routes import auth, health, items
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics_auth import require_metrics_token


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.
    Reads settings via get_settings() so tests can override via cache replacement.
    """
    from app.core.config import get_settings as _get_settings
    settings = _get_settings()
    configure_logging(settings.environment)
    logger = get_logger(__name__)

    logger.info(
        "application_starting",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )

    yield

    logger.info("application_shutdown")


def create_app() -> FastAPI:
    """
    Application factory.

    Using a factory function (rather than a module-level app object)
    makes the application testable — tests can call create_app() with
    different settings injected via dependency overrides.
    """
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "A FastAPI application demonstrating secure API design patterns. "
            "Built as the target application for a DevSecOps CI/CD pipeline."
        ),
        lifespan=lifespan,
        # Disable docs in production — reduces attack surface
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
    )

    # CORS — only allow configured origins
    # In production this should be your actual frontend domain(s)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Register routers
    # Health endpoints at root level — not versioned (Kubernetes probes
    # should not need to know about API versioning)
    app.include_router(health.router)

    # API v1 routes
    api_v1_prefix = "/api/v1"
    app.include_router(auth.router, prefix=api_v1_prefix)
    app.include_router(items.router, prefix=api_v1_prefix)

    # Record request metrics, and expose them at /metrics (bearer token required).
    # We serve /metrics ourselves rather than via Instrumentator.expose(): that
    # helper ignores the scraper's Accept header and always labels the body
    # "text/plain; version=1.0.0", which many scrapers (Grafana Cloud, Alloy,
    # older Prometheus) reject. choose_encoder negotiates a format the scraper
    # supports and falls back to the classic text format (version=0.0.4).
    Instrumentator().instrument(app)

    @app.get(
        "/metrics",
        include_in_schema=False,
        dependencies=[Depends(require_metrics_token)],
    )
    def metrics(request: Request) -> Response:
        encoder, content_type = choose_encoder(request.headers.get("Accept", ""))
        return Response(content=encoder(REGISTRY), media_type=content_type)

    # Global exception handlers
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Catch-all handler for unhandled exceptions.

        Returns a generic error message — never exposes internal
        error details to clients in production.
        """
        logger = get_logger(__name__)
        logger.error(
            "unhandled_exception",
            path=str(request.url.path),
            method=request.method,
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An internal error occurred"},
        )

    return app


# Module-level app instance for uvicorn / gunicorn
app = create_app()
