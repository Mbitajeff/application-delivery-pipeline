# Secure Pipeline API

A production-grade DevSecOps CI/CD pipeline for a Python FastAPI application.  
Security is built into every stage — not bolted on at the end.

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
| Test coverage | pytest, 97%+ coverage, 93 tests |
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
│   └── tests/                Unit + integration tests (93 tests)
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
│   ├── pipeline.yml          Main CI/CD pipeline (10 jobs)
│   ├── pr-checks.yml         Fast PR feedback (3 jobs)
│   └── scheduled-scan.yml    Weekly rescan (2 jobs)
│
├── docs/
│   ├── ARCHITECTURE.md       Security architecture diagrams
│   ├── PIPELINE.md           Stage documentation with rationale
│   ├── CLOUD_EXTENSION.md    How to add AWS / Azure / GCP
│   └── RUNBOOK.md            Operational procedures
│
├── Dockerfile                Multi-stage, non-root, read-only FS compatible
├── docker-compose.yml        Local development
├── docker-compose.test.yml   DAST target environment
└── Makefile                  Local commands mirroring CI
```

---

## Pipeline stages

```
pre-flight        Gitleaks secret scan + Ruff lint
├── sast          Semgrep SAST (OWASP Top 10 + custom rules)
├── sca           Trivy filesystem scan (dependency CVEs)
└── iac-scan      Checkov IaC scan (Terraform + Kubernetes)
     └── test     pytest + 80% coverage gate
          └── build   Docker multi-stage build
               ├── image-scan   Trivy image scan (Critical CVEs → FAIL)
               └── sbom-sign    Syft SBOM + Cosign keyless signing
                    └── push    Push to GHCR
                         └── dast    OWASP ZAP baseline (warn-only)
                              └── deploy-gate    Manual approval
```

---

## Documentation

- [Security Architecture](docs/ARCHITECTURE.md)
- [Pipeline Stage Rationale](docs/PIPELINE.md)
- [Threat Model](security/THREAT_MODEL.md)
- [Vulnerability Remediation](security/VULNERABILITY_REMEDIATION.md)
- [Cloud Extension Guide](docs/CLOUD_EXTENSION.md)
- [Operational Runbook](docs/RUNBOOK.md)

---

## Extending to cloud

See [docs/CLOUD_EXTENSION.md](docs/CLOUD_EXTENSION.md).

The application, security gates, SBOM, and signing are identical across AWS, Azure, and GCP.  
Only the registry, Kubernetes runtime, Terraform provider, and OIDC trust relationship change.

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
