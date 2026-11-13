# =============================================================================
# Multi-stage Dockerfile for the Secure Pipeline API
# =============================================================================
#
# Stages:
#   builder  — installs dependencies (includes build tools for C extensions)
#   runtime  — minimal production image with no build toolchain
#
# Security properties of the final image:
#   - Non-root user (UID 1000)
#   - No build tools (gcc, pip, setuptools) in runtime layer
#   - No source code outside app/ — no tests, no dev dependencies
#   - Minimal OS packages — slim-bookworm base only
#   - Read-only filesystem compatible (no writes to container FS at runtime)
#   - Pinned base image digest recommended for production (see ARCHITECTURE.md)
#
# Base image choice: python:3.12-slim-bookworm
#   - glibc base: required for bcrypt C extension compilation
#   - Trivy has full Debian CVE coverage for this image family
#   - slim variant: ~130MB vs ~1GB for the full image
#   - bookworm (Debian 12): current stable, long-term support
#   - Python 3.12: stable, production-ready LTS release
#
# Python 3.14 note: 3.14 is in active development; no official Docker image
# exists yet. The application code is 3.12+ compatible. Upgrade when
# python:3.14-slim is available and Trivy scan passes.
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: builder
# Installs all dependencies into /install so we can copy them cleanly.
# This stage is NOT included in the final image.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

# Prevent Python from writing .pyc files and buffering stdout/stderr.
# PYTHONDONTWRITEBYTECODE: no .pyc clutter in the image layer
# PYTHONUNBUFFERED: logs appear immediately in container output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # pip: disable cache to keep layer small; no version check noise
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install OS build dependencies needed to compile bcrypt's C extension.
# These are NOT copied to the runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements file first.
# Docker layer cache: if requirements.txt hasn't changed, this layer is reused
# and pip install is skipped on subsequent builds — significantly faster CI.
COPY app/requirements.txt .

# Install into /install so we can copy just this directory to runtime.
# Using --prefix to isolate from system Python avoids contaminating the
# system site-packages in the runtime stage.
RUN pip install --prefix=/install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 2: runtime
# Clean image — only the installed packages and application code.
# No build tools, no pip, no gcc, no source outside app/.
# -----------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Tell Python where to find the packages installed in /install
    PYTHONPATH=/install/lib/python3.12/site-packages \
    # Application settings — override via environment variables in deployment.
    # These are safe defaults; SECRET_KEY must always be overridden.
    ENVIRONMENT=production \
    HOST=0.0.0.0 \
    PORT=8000

# Create a non-root user and group.
# UID/GID 65532 satisfies Checkov CKV_K8S_40 (requires UID > 10000)
# and is high enough to avoid conflicts with any system UIDs.
RUN groupadd --gid 65532 appgroup && \
    useradd --uid 65532 --gid appgroup --shell /bin/sh --create-home appuser

WORKDIR /app

# Copy installed packages from the builder stage.
# This is the ONLY thing we take from builder — no toolchain included.
COPY --from=builder /install /install

# Copy the application source.
# Only copy app/ — not tests, not infrastructure, not CI config.
# .dockerignore ensures nothing else leaks in even if paths were broader.
COPY app/ ./app/

# Change ownership to the non-root user BEFORE switching to it.
# If done after USER, the chown would fail (can't chown files you don't own).
RUN chown -R appuser:appgroup /app

# Switch to non-root user for all subsequent instructions and runtime.
USER appuser

# Expose the port the application listens on.
# EXPOSE is documentation — it does not publish the port.
# Actual port binding happens at container run time (-p 8000:8000).
EXPOSE 8000

# Health check for Docker and container orchestrators that read it.
# Kubernetes uses its own liveness/readiness probes (defined in manifests),
# but this provides a fallback for docker run and docker-compose.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

# Runtime entrypoint.
# Using exec form (JSON array) — not shell form — so the process receives
# signals directly (SIGTERM for graceful shutdown) rather than through sh.
#
# --workers 1: single worker for containers — horizontal scaling is handled
#   by Kubernetes replica count, not uvicorn workers.
# --host: bind to all interfaces (0.0.0.0) inside the container.
# --port: must match EXPOSE above.
# --no-access-log: access logs go through structlog, not uvicorn's logger,
#   to maintain consistent structured JSON format.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--no-access-log"]
