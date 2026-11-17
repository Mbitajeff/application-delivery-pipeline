# Secure Pipeline API

A production-grade DevSecOps CI/CD pipeline for a Python FastAPI application.  
Security is built into every stage — not bolted on at the end.

---

## Pipeline Architecture

```mermaid
flowchart TD
    DEV(["Developer\nPush to main"])

    subgraph PREFLIGHT ["Pre-flight"]
        GL["Gitleaks\nSecret Scanning"]
        RUFF["Ruff\nLint"]
    end

    subgraph PARALLEL ["Parallel Security Analysis"]
        SAST["Semgrep\nSAST · OWASP Top 10"]
        SCA["Trivy\nDependency CVEs"]
        IAC["Checkov\nTerraform + Kubernetes"]
    end

    subgraph QUALITY ["Quality Gate"]
        PYTEST["pytest\n93 tests · 97.5% coverage"]
    end

    subgraph CONTAINER ["Container Pipeline"]
        DOCKER["Docker\nMulti-stage Build"]
        ISCAN["Trivy\nImage CVE Scan"]
        SBOM["Syft\nSBOM Generation"]
        SIGN["Cosign\nKeyless Signing · Sigstore"]
    end

    REGISTRY(["GHCR\nSigned Image + SBOM"])

    subgraph RUNTIME ["Runtime Testing"]
        ZAP["OWASP ZAP\nDAST Baseline Scan"]
    end

    GATE(["Manual Approval\nDeployment Gate"])

    DEV --> PREFLIGHT
    PREFLIGHT --> PARALLEL
    PARALLEL --> QUALITY
    QUALITY --> CONTAINER
    DOCKER --> ISCAN
    DOCKER --> SBOM
    SBOM --> SIGN
    ISCAN --> REGISTRY
    SIGN --> REGISTRY
    REGISTRY --> RUNTIME
    RUNTIME --> GATE
```

---

## Trust Boundary Model

```mermaid
flowchart LR
    subgraph B1 ["Boundary 1 · Developer Workstation"]
        DEV["Developer"]
        PRECOMMIT["pre-commit\nGitleaks hook"]
        DEV --> PRECOMMIT
    end

    subgraph B2 ["Boundary 2 · GitHub CI"]
        RUNNER["Ephemeral Runner\nMinimal permissions per job"]
        GATES["8 Security Gates\nBlocking on failure"]
        RUNNER --> GATES
    end

    subgraph B3 ["Boundary 3 · Registry"]
        GHCR["GHCR\nSigned · SBOM attached"]
    end

    subgraph B4 ["Boundary 4 · Kubernetes"]
        PSS["Pod Security Standard\nrestricted"]
        NP["NetworkPolicy\ndefault-deny"]
        RBAC["RBAC\nleast-privilege"]
    end

    B1 -->|"git push"| B2
    B2 -->|"OIDC token\nauthenticated push"| B3
    B3 -->|"signature verified\nbefore admission"| B4
```

---

## Security Gates

```mermaid
flowchart LR
    S1["SEC-01\nGitleaks\nSecret Scan\nBLOCKS"]
    S2["SEC-02\nSemgrep\nSAST\nBLOCKS High"]
    S3["SEC-03\nTrivy SCA\nDep CVEs\nBLOCKS Crit/High"]
    S4["SEC-04\nCheckov\nIaC Scan\nBLOCKS High"]
    S5["SEC-05\nTrivy Image\nCVE Scan\nBLOCKS Critical"]
    S6["SEC-06\nSyft\nSBOM\nArtifact"]
    S7["SEC-07\nCosign\nSigning\nArtifact"]
    S8["SEC-08\nOWASP ZAP\nDAST\nWARN"]

    S1 --> S2 --> S3 --> S4 --> S5 --> S6 --> S7 --> S8
```

---

## Monitoring and Dashboards

The deployed API exposes Prometheus metrics at `/metrics`. Grafana Cloud scrapes them every 30 seconds, and a dashboard-as-code export lives in the repo.

```mermaid
flowchart LR
    APP["FastAPI app\n(Render)\n/metrics"]
    AUTH{"Bearer token\nMETRICS_TOKEN"}
    SCRAPE["Grafana Cloud\nMetrics Endpoint\nscrape job (30s)"]
    PROM[("Grafana Cloud\nPrometheus")]
    DASH["Dashboard\nSecure Pipeline API"]
    DENY["401\nNot authenticated"]

    SCRAPE -->|"Authorization: Bearer ..."| AUTH
    AUTH -->|valid| APP
    AUTH -->|missing / wrong| DENY
    APP -->|metrics| SCRAPE
    SCRAPE --> PROM --> DASH
```

**Dashboard:** [`monitoring/grafana/secure-pipeline-api.json`](monitoring/grafana/secure-pipeline-api.json)

![Grafana dashboard: overview and traffic](docs/images/grafana-dashboard-overview.png)

![Grafana dashboard: latency, security and runtime](docs/images/grafana-dashboard-latency-security.png)

| Section | Panels |
|---------|--------|
| Overview | Requests/sec, 5xx error rate, p95 latency, memory (RSS) |
| Traffic | Requests/sec by route, requests/sec by status class |
| Latency | p50 / p95 / p99 across all routes, average latency per route |
| Security and runtime | 4xx responses by route, rejected `/metrics` scrapes, CPU and memory |

Traffic panels exclude the scraper's own `/metrics` requests, so they show real API usage only. The memory tile turns orange at 400 MiB and red at 480 MiB (Render free tier limit: 512 MB).

### Securing `/metrics`

`/metrics` exposes route names, traffic volumes and runtime details, so it is not public:

- Requests must send `Authorization: Bearer <METRICS_TOKEN>` (constant-time comparison)
- **Fail-closed:** if `METRICS_TOKEN` is unset, every request gets `401`
- Excluded from the OpenAPI schema
- The response format is negotiated from the scraper's `Accept` header (falls back to Prometheus text format `0.0.4`)
- The dashboard's *Rejected /metrics scrapes* panel records every unauthenticated attempt

### Setup

1. Generate a token and set it as `METRICS_TOKEN` in the hosting environment (Render → Environment). Never commit it.
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. In Grafana Cloud: **Connections → Add new connection → Metrics Endpoint**
   - URL: `https://<your-app>/metrics`
   - Authentication: **Bearer**, paste the token (without the `Bearer` prefix)
3. Import the dashboard: **Dashboards → New → Import**, upload `monitoring/grafana/secure-pipeline-api.json`, and select your Grafana Cloud Prometheus data source.

> Grafana Cloud's hosted scraper does not emit the `up` metric. Use `http_requests_total` or `python_info` to check that data is arriving.

---

## What this project demonstrates

| Capability | Implementation |
|------------|---------------|
| Secure API design | FastAPI + JWT auth + IDOR protection + Pydantic validation |
| Secret scanning | Gitleaks (pre-commit + CI) |
| Static analysis | Semgrep (OWASP Top 10 + custom rules) |
| Dependency CVEs | Trivy SCA |
| IaC security | Checkov (Terraform + Kubernetes) |
| Container hardening | Multi-stage Dockerfile, non-root, read-only filesystem |
| Container CVEs | Trivy image scan |
| Software Bill of Materials | Syft (SPDX JSON format) |
| Image signing + provenance | Cosign keyless via GitHub OIDC + Sigstore |
| Dynamic testing | OWASP ZAP baseline scan |
| Infrastructure | Terraform modules (networking + EKS) + Kubernetes manifests |
| Kubernetes security | NetworkPolicy default-deny, RBAC, Pod Security Standards |
| Test coverage | pytest, 97%+ coverage, 101 tests |
| Observability | Prometheus `/metrics` (token-protected) + Grafana Cloud dashboard as code |
| Deployment gate | GitHub Environments with manual approval |
| Scheduled rescanning | Weekly Trivy rescan of published images |
| Multi-cloud portability | GHCR now; ECR/ACR/GAR with minimal changes |

---

## Quick start

```bash
# Install dependencies (requires Python 3.12+)
pip install -e ".[dev]"

# Run tests
python -m pytest

# Run linter
python -m ruff check app/

# Start the development server
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# API: http://127.0.0.1:8000
# Docs: http://127.0.0.1:8000/docs
```

---

## Project structure

```
├── app/                      FastAPI application
│   ├── api/                  Route handlers and dependency injection
│   ├── core/                 Config, JWT security, logging
│   ├── models/               Pydantic request/response models
│   └── tests/                Unit + integration tests (101 tests)
│
├── monitoring/
│   └── grafana/              Grafana dashboard (JSON, dashboard as code)
│
├── infrastructure/
│   ├── terraform/            Terraform modules (networking, EKS)
│   └── kubernetes/           Kubernetes manifests
│
├── security/
│   ├── .gitleaks.toml        Secret scanning config
│   ├── .semgrep.yml          SAST rules
│   ├── checkov-config.yaml   IaC scanning config
│   ├── zap-config.yaml       DAST config
│   ├── THREAT_MODEL.md       Threat model and trust boundaries
│   └── VULNERABILITY_REMEDIATION.md  Response runbook per gate
│
├── .github/workflows/
│   ├── pipeline.yml          Main CI/CD pipeline (9 jobs)
│   ├── pr-checks.yml         Fast PR feedback
│   ├── scheduled-scan.yml    Weekly rescan
│   └── pages.yml             Documentation site
│
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PIPELINE.md
│   ├── CLOUD_EXTENSION.md
│   ├── RUNBOOK.md
│   └── images/               README screenshots
│
├── Dockerfile                Multi-stage, non-root, read-only FS
├── docker-compose.yml        Local development
└── Makefile                  Local commands mirroring CI
```

---

## Documentation

- [Security Architecture](docs/ARCHITECTURE.md)
- [Pipeline Stage Rationale](docs/PIPELINE.md)
- [Threat Model](security/THREAT_MODEL.md)
- [Vulnerability Remediation](security/VULNERABILITY_REMEDIATION.md)
- [Cloud Extension Guide](docs/CLOUD_EXTENSION.md)
- [Operational Runbook](docs/RUNBOOK.md)
- 📖 [Live Docs Site](https://mbitajeff.github.io/application-delivery-pipeline)

---

## Technology stack

| Category | Technology |
|----------|-----------|
| Application | Python 3.12, FastAPI, Pydantic, bcrypt, python-jose |
| Testing | pytest, pytest-cov, httpx2 |
| Linting | Ruff |
| Container | Docker (multi-stage), python:3.12-slim-bookworm |
| Orchestration | Kubernetes (Kind locally) |
| Infrastructure | OpenTofu (Terraform-compatible) |
| Registry | GHCR |
| Secret scanning | Gitleaks |
| SAST | Semgrep |
| SCA | Trivy |
| IaC scanning | Checkov |
| SBOM | Syft |
| Image signing | Cosign + Sigstore |
| DAST | OWASP ZAP |
| CI/CD | GitHub Actions |
| Metrics | prometheus-client, prometheus-fastapi-instrumentator |
| Dashboards | Grafana Cloud (Metrics Endpoint scrape job) |
