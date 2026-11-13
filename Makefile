# =============================================================================
# Makefile — local development commands that mirror CI pipeline steps
# =============================================================================
#
# Every CI pipeline stage has a corresponding make target.
# Running `make pipeline` locally simulates the full CI run.
#
# Prerequisites:
#   - Python 3.12+ with pip
#   - Docker (for image-* and dast targets)
#   - Kind (for kind-* targets)
#   - OpenTofu (for tf-* targets)
#
# Targets marked [REQUIRES DOCKER] will fail without Docker installed.
# =============================================================================

.PHONY: help install test lint scan scan-secrets scan-sast scan-sca \
        build image-build image-scan image-sign \
        kind-up kind-deploy kind-down \
        tf-validate tf-scan \
        dast pipeline clean

# Default target — show help
help:
	@echo ""
	@echo "Secure Pipeline API — Available Make Targets"
	@echo "============================================="
	@echo ""
	@echo "  Development:"
	@echo "    install       Install all dependencies (editable)"
	@echo "    test          Run test suite with coverage"
	@echo "    lint          Run Ruff linter"
	@echo "    run           Start the development server"
	@echo ""
	@echo "  Security scanning (no Docker required):"
	@echo "    scan-secrets  Run Gitleaks secret scan"
	@echo "    scan-sast     Run Semgrep SAST"
	@echo "    scan-sca      Run Trivy filesystem/dependency scan"
	@echo "    scan-iac      Run Checkov IaC scan"
	@echo "    scan          Run all scans (secrets + sast + sca + iac)"
	@echo ""
	@echo "  Container (REQUIRES DOCKER):"
	@echo "    image-build   Build the Docker image"
	@echo "    image-scan    Scan the built image with Trivy"
	@echo "    image-sign    Sign the image with Cosign (keyless)"
	@echo "    image-push    Push the image to GHCR"
	@echo ""
	@echo "  Kubernetes (REQUIRES DOCKER + KIND):"
	@echo "    kind-up       Create a local Kind cluster"
	@echo "    kind-deploy   Deploy the application to Kind"
	@echo "    kind-down     Destroy the Kind cluster"
	@echo ""
	@echo "  DAST (REQUIRES DOCKER):"
	@echo "    dast          Run OWASP ZAP baseline scan"
	@echo ""
	@echo "  Full pipeline simulation:"
	@echo "    pipeline      Run all non-Docker pipeline stages locally"
	@echo "    pipeline-full Run all stages including Docker (requires Docker)"
	@echo ""
	@echo "  Cleanup:"
	@echo "    clean         Remove build artifacts and caches"
	@echo ""

# =============================================================================
# Development
# =============================================================================

install:
	pip install -e ".[dev]"

test:
	python -m pytest

test-no-cov:
	python -m pytest --no-cov

lint:
	python -m ruff check app/

lint-fix:
	python -m ruff check app/ --fix

run:
	python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# =============================================================================
# Security scanning — no Docker required
# =============================================================================

scan-secrets:
	@echo "--- Secret Scanning (Gitleaks) ---"
	gitleaks detect --source . --config security/.gitleaks.toml --verbose

scan-sast:
	@echo "--- SAST (Semgrep) ---"
	semgrep scan --config security/.semgrep.yml app/ --error

scan-sca:
	@echo "--- SCA (Trivy filesystem) ---"
	trivy fs . \
		--scanners vuln \
		--exit-code 1 \
		--severity CRITICAL,HIGH \
		--ignorefile security/.trivyignore \
		--format table

scan-iac:
	@echo "--- IaC Scan (Checkov) ---"
	checkov -d infrastructure/ \
		--config-file security/checkov-config.yaml \
		--output cli \
		--compact

scan: scan-secrets scan-sast scan-sca scan-iac
	@echo "--- All scans complete ---"

# =============================================================================
# Container — REQUIRES DOCKER
# =============================================================================

IMAGE_NAME ?= secure-pipeline-api
IMAGE_TAG  ?= dev
REGISTRY   ?= ghcr.io
REPO       ?= $(GITHUB_REPOSITORY)

image-build:
	@echo "--- Docker Build (multi-stage) ---"
	docker build \
		--target runtime \
		--tag $(IMAGE_NAME):$(IMAGE_TAG) \
		--tag $(IMAGE_NAME):latest \
		--label "org.opencontainers.image.revision=$(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
		--label "org.opencontainers.image.created=$(shell date -u +%Y-%m-%dT%H:%M:%SZ)" \
		.

image-scan:
	@echo "--- Image Scan (Trivy) ---"
	trivy image \
		--exit-code 1 \
		--severity CRITICAL \
		--format table \
		$(IMAGE_NAME):$(IMAGE_TAG)

image-sign:
	@echo "--- Image Signing (Cosign keyless) ---"
	cosign sign --yes $(REGISTRY)/$(REPO):$(IMAGE_TAG)

image-push:
	@echo "--- Push to Registry ---"
	docker tag $(IMAGE_NAME):$(IMAGE_TAG) $(REGISTRY)/$(REPO):$(IMAGE_TAG)
	docker push $(REGISTRY)/$(REPO):$(IMAGE_TAG)

image-run:
	@echo "--- Run container locally ---"
	docker compose up --build

image-run-detached:
	docker compose up --build -d

image-stop:
	docker compose down

# =============================================================================
# Kubernetes (Kind) — REQUIRES DOCKER + KIND
# =============================================================================

KIND_CLUSTER ?= secure-pipeline

kind-up:
	@echo "--- Creating Kind cluster ---"
	kind create cluster --name $(KIND_CLUSTER) --config infrastructure/kubernetes/kind-config.yaml
	kubectl cluster-info --context kind-$(KIND_CLUSTER)

kind-load:
	@echo "--- Loading image into Kind ---"
	kind load docker-image $(IMAGE_NAME):$(IMAGE_TAG) --name $(KIND_CLUSTER)

kind-deploy:
	@echo "--- Deploying to Kind ---"
	kubectl apply -f infrastructure/kubernetes/namespace.yaml
	kubectl apply -f infrastructure/kubernetes/rbac.yaml
	kubectl apply -f infrastructure/kubernetes/configmap.yaml
	kubectl apply -f infrastructure/kubernetes/deployment.yaml
	kubectl apply -f infrastructure/kubernetes/service.yaml
	kubectl apply -f infrastructure/kubernetes/networkpolicy.yaml
	kubectl rollout status deployment/secure-pipeline-api -n secure-pipeline

kind-status:
	kubectl get all -n secure-pipeline

kind-logs:
	kubectl logs -l app=secure-pipeline-api -n secure-pipeline --follow

kind-down:
	kind delete cluster --name $(KIND_CLUSTER)

# =============================================================================
# Terraform — no Docker required
# =============================================================================

tf-validate:
	@echo "--- Terraform Validate ---"
	cd infrastructure/terraform/environments/local && tofu validate

tf-scan:
	@echo "--- Terraform IaC Scan (Checkov) ---"
	checkov -d infrastructure/terraform \
		--config-file security/checkov-config.yaml

# =============================================================================
# DAST — REQUIRES DOCKER
# =============================================================================

dast:
	@echo "--- DAST (OWASP ZAP baseline scan) ---"
	docker compose -f docker-compose.test.yml up -d
	sleep 15
	docker run --rm \
		--network host \
		-v $(PWD)/security/zap-config.yaml:/zap/config.yaml:ro \
		-v $(PWD)/security/zap-reports:/zap/reports \
		ghcr.io/zaproxy/zaproxy:stable \
		zap-baseline.py \
			-t http://localhost:8000 \
			-c /zap/config.yaml \
			-r /zap/reports/zap-report.html \
			-J /zap/reports/zap-report.json \
			-I
	docker compose -f docker-compose.test.yml down

# =============================================================================
# Full pipeline simulation
# =============================================================================

# Runs all non-Docker stages — works without Docker installed
pipeline: install lint test scan-sca scan-iac
	@echo ""
	@echo "=== Local pipeline complete (no-Docker stages) ==="
	@echo "    Remaining pipeline stages (require Docker):"
	@echo "    - image-build, image-scan, image-sign, image-push"
	@echo "    - dast"
	@echo "    These run automatically in GitHub Actions."
	@echo ""

# Full pipeline including Docker stages
pipeline-full: pipeline image-build image-scan dast
	@echo "=== Full pipeline complete ==="

# =============================================================================
# Cleanup
# =============================================================================

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -f coverage.xml .coverage 2>/dev/null || true
	@echo "Cleaned build artifacts."
