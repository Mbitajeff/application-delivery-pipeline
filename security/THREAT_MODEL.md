# Threat Model — Secure Pipeline API

**Version:** 1.0  
**Date:** November 2026  
**Status:** Living document — review quarterly and after any significant architecture change.

---

## 1. System Overview

The Secure Pipeline API is a FastAPI application deployed on Kubernetes. It provides a REST API for managing items with JWT-based authentication. It is built, tested, scanned, signed, and deployed through a GitHub Actions CI/CD pipeline.

The system exists primarily as a demonstration of DevSecOps security controls. The threat model covers both the application and the pipeline that builds and deploys it.

---

## 2. Assets

| Asset | Sensitivity | Compromise impact |
|-------|------------|-------------------|
| Application source code | High | Vulnerabilities embedded in all deployments |
| JWT signing secret (`SECRET_KEY`) | Critical | Full authentication bypass; all tokens forgeable |
| User passwords (bcrypt hashes) | High | Offline cracking attack if DB is compromised |
| Container image (signed) | High | Tampered image deployed to production |
| Terraform state | High | Infrastructure layout, resource IDs exposed |
| GitHub Actions secrets | Critical | Pipeline can be weaponised to push malicious images |
| SBOM and scan reports | Medium | Compliance evidence; tampering undermines audit trail |
| Kubernetes RBAC configuration | High | Privilege escalation if misconfigured |

---

## 3. Trust Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│  Boundary 1: Developer workstation                              │
│  Trust level: Medium (assumed non-hostile, possibly compromised)│
│                                                                  │
│  Developer writes code → pre-commit hooks run Gitleaks          │
│  Developer pushes → GitHub branch protection enforces PR review │
└──────────────────────────────┬──────────────────────────────────┘
                               │ git push (HTTPS/SSH)
┌──────────────────────────────▼──────────────────────────────────┐
│  Boundary 2: GitHub (source control + CI)                       │
│  Trust level: High (GitHub is trusted infrastructure)           │
│                                                                  │
│  GitHub Actions runners: ephemeral, managed, no persistent state│
│  GITHUB_TOKEN: scoped per job (minimal permissions)             │
│  Actions: pinned to full SHA — not mutable tags                 │
│  Environments: production requires manual approval              │
└──────────────────────────────┬──────────────────────────────────┘
                               │ OCI push (authenticated)
┌──────────────────────────────▼──────────────────────────────────┐
│  Boundary 3: Container registry (GHCR)                          │
│  Trust level: High (GitHub-managed)                             │
│                                                                  │
│  Images: signed with Cosign (keyless via GitHub OIDC)           │
│  SBOMs: attached as attestations                                │
│  Scan results: attached as attestations                         │
└──────────────────────────────┬──────────────────────────────────┘
                               │ Image pull (signature verified)
┌──────────────────────────────▼──────────────────────────────────┐
│  Boundary 4: Kubernetes cluster                                  │
│  Trust level: Medium (depends on correct configuration)         │
│                                                                  │
│  Admission controller: rejects unsigned images (future)         │
│  Pod Security Standard: restricted                              │
│  NetworkPolicy: default deny-all                                │
│  RBAC: least-privilege service account                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Attack Vectors and Controls

### 4.1 Source Code Attacks

| Attack | Vector | Control | Gate |
|--------|--------|---------|------|
| Hardcoded secret in code | Developer commits secret | Gitleaks pre-commit + CI scan | SEC-01, blocks |
| Insecure code pattern | Developer writes vulnerable code | Semgrep SAST | SEC-02, blocks on High |
| Vulnerable dependency | `requirements.txt` update | Trivy SCA | SEC-03, blocks on Critical/High |
| Malicious PR from fork | Pull request to main | Branch protection + PR review required | Manual gate |

### 4.2 Container Attacks

| Attack | Vector | Control | Gate |
|--------|--------|---------|------|
| Vulnerable base image | `FROM python:3.12-slim-bookworm` | Trivy image scan | SEC-04, blocks on Critical |
| Running as root | Dockerfile `USER` omitted | Non-root USER + K8s `runAsNonRoot: true` | Dockerfile + PSS |
| Supply chain: tampered image | Registry compromise | Cosign signature verification | SEC-07 |
| Container escape via capabilities | Over-privileged container | `capabilities: drop: [ALL]` | K8s manifest |

### 4.3 Pipeline Attacks

| Attack | Vector | Control |
|--------|--------|---------|
| Malicious GitHub Action (tag mutation) | Action updated after pin | All actions pinned to full commit SHA |
| Script injection via PR title | `${{ github.event.pull_request.title }}` in shell | Never interpolate `github.event.*` into shell commands |
| Exfiltration via CI logs | `echo $SECRET` in step | Secrets never echoed; `::add-mask::` where needed |
| Fork PR accessing production secrets | `pull_request_target` abuse | No `pull_request_target`; secrets not available to fork PRs |
| Overly broad GITHUB_TOKEN | Default permissions | `permissions: read-all` global; write only where required |

### 4.4 Infrastructure Attacks

| Attack | Vector | Control | Gate |
|--------|--------|---------|------|
| Public S3 bucket | Terraform misconfiguration | Checkov scan | SEC-05, blocks |
| SSH port open to internet | Security group rule | Checkov CKV_AWS_25 | SEC-05, blocks |
| EKS secrets unencrypted | Missing encryption config | Checkov CKV_AWS_58 | SEC-05, blocks |
| EC2 IMDSv1 (SSRF to metadata) | Missing IMDSv2 requirement | Checkov CKV_AWS_341 | SEC-05, blocks |
| Unencrypted EBS | Missing encryption | Checkov CKV_AWS_8 | SEC-05, blocks |

### 4.5 Runtime Attacks

| Attack | Vector | Control | Gate |
|--------|--------|---------|------|
| SQL injection | Unsanitised input to query | Pydantic validation + no raw SQL | SAST + DAST |
| XSS | Unsanitised output | Pydantic output models; CSP header | DAST |
| Broken authentication | JWT implementation flaw | unit tests for token signing, expiry, tampering | Test suite |
| IDOR | Accessing other users' items | `owner_id` server-side; ownership check on mutation | IDOR tests |
| User enumeration | Different errors for wrong user vs password | Generic 401 for all auth failures | Integration tests |
| Brute force login | Repeated auth attempts | Rate limiting at Ingress (nginx annotation) | Ingress config |
| Pod-to-pod lateral movement | Compromised pod reaches other pods | NetworkPolicy default-deny | K8s manifest |

---

## 5. What This Pipeline Cannot Catch

Being honest about limitations is part of a good threat model.

| Gap | Why | Mitigation |
|-----|-----|------------|
| Zero-day CVEs | Not in vulnerability databases yet | Runtime detection (Falco), weekly rescans |
| Logic vulnerabilities | SAST/DAST cannot reason about business logic | Code review, security design review |
| Social engineering | Outside technical scope | Security awareness training |
| Insider threat with git access | A determined insider can bypass pre-commit hooks | Branch protection requires PR review from second person |
| Infrastructure-layer DAST findings | ZAP scans a Docker service, not a cloud-deployed app | Promote DAST to staging environment when available |
| Supply chain: compromised PyPI package | Trivy only catches known CVEs | Pin all dependencies; monitor PyPI security advisories |
| Kubernetes admission policy (image signature) | Not yet implemented | Future: Kyverno policy to verify Cosign signature on admission |

---

## 6. Residual Risk

After all controls are applied, the following residual risks remain accepted:

| Risk | Accepted because |
|------|-----------------|
| Public Sigstore transparency log exposes image metadata | Public repository; image metadata is not sensitive |
| bcrypt work factor (12 rounds) may become insufficient | Configurable via `_BCRYPT_ROUNDS`; review annually |
| JWT HS256 single-secret | No rotation mechanism yet; acceptable for initial phase |
| DAST is warn-only initially | Baseline must be established before blocking |

---

## 7. Review Cadence

| Trigger | Action |
|---------|--------|
| Quarterly | Review skip list in `checkov-config.yaml`; review residual risks |
| New dependency added | Re-run Trivy SCA; verify no new CRITICAL/HIGH |
| Security incident | Update relevant section with findings and new controls |
| Architecture change | Full threat model review before deployment |
