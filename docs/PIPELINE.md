# Pipeline Documentation

Every stage in the pipeline exists for a specific reason. This document explains what each stage does, why it exists, what it blocks on, and what it cannot catch.

---

## Pipeline Overview

```
pre-flight → [sast + sca + iac-scan] → test → build → [image-scan + sbom-sign] → push → dast → deploy-gate
```

Parallel stages (in brackets) run simultaneously to minimise total pipeline time.  
Estimated total runtime: **~15 minutes** on a cold GitHub Actions runner.

---

## Stage 1: pre-flight

**Jobs:** `pre-flight`  
**Runs:** On every push to `main` and every PR  
**Blocks on:** Any secret found, any lint error  

### Secret scanning (Gitleaks)

**Why:** Secrets committed to git cannot be fully removed — they persist in history. Even `git reset` and force-push leave the secret exposed to anyone who cloned before the removal. Prevention at commit time is the only reliable control.

**What it scans:** Full git history (not just the latest commit). A secret committed three months ago and "deleted" in a later commit is still found.

**What it cannot catch:** Secrets stored in environment variables, CI pipeline variables, or secrets managers. Those are handled by the dependency on OIDC authentication and GitHub Environments.

**Configuration:** `security/.gitleaks.toml`

### Lint (Ruff)

**Why:** Lint failures that make it to the build stage waste runner minutes and obscure real failures. Catching them early costs less.

**What it checks:** Import sorting, unused imports, common bugs (flake8-bugbear), security patterns (bandit rules), Python upgrade opportunities.

---

## Stage 2: Parallel security analysis

**Jobs:** `sast`, `sca`, `iac-scan` (all three in parallel)  
**Runs:** After pre-flight passes  
**Blocks on:** See individual gates below  

### SAST — Semgrep

**Why:** Finds security vulnerabilities in source code without running it. SAST catches issues that are architecturally present (a function that accepts unsanitised input, a JWT without expiry check) regardless of whether a test exercises that code path.

**What it catches:** OWASP Top 10 patterns, insecure Python idioms, JWT implementation weaknesses, hardcoded secrets the Gitleaks pattern didn't match.

**Blocks on:** HIGH and ERROR severity findings.

**What it cannot catch:** Logic vulnerabilities (business logic flaws), runtime-only issues, vulnerabilities in dependencies (that's SCA's job).

**Configuration:** `security/.semgrep.yml` + `p/owasp-top-ten`, `p/python`, `p/jwt`

### SCA — Trivy filesystem scan

**Why:** Third-party dependencies are the most common source of known CVEs. `requests`, `python-jose`, `bcrypt` — any of these could have a known vulnerability. SCA checks the declared dependency manifest against public vulnerability databases.

**What it catches:** CVEs in direct and transitive Python dependencies. Also catches secrets that were missed by Gitleaks.

**Blocks on:** CRITICAL and HIGH severity CVEs with available fixes. `ignore-unfixed: true` means CVEs with no patch available do not block — they're noted but not actionable.

**What it cannot catch:** Zero-day vulnerabilities, logic flaws in dependencies, malicious packages (supply chain attacks where the package itself is malicious but has no CVE).

**Configuration:** `security/.trivyignore` for documented exceptions.

### IaC scan — Checkov

**Why:** Infrastructure misconfigurations are exploitable before the application is deployed. An S3 bucket declared as public in Terraform will be public when `terraform apply` runs. Catching it in the pipeline prevents the misconfiguration from ever reaching a real cloud environment.

**What it catches:** AWS security group rules exposing SSH to the internet, unencrypted EBS volumes, missing EKS secrets encryption, Kubernetes pods running as root, missing readiness probes, containers with no resource limits.

**Blocks on:** HIGH severity findings. See `security/checkov-config.yaml` for documented skip rules.

**What it cannot catch:** Drift between declared and actual infrastructure (requires runtime scanning with AWS Config or Terraform plan diffing). Misconfigurations introduced through the cloud console that aren't reflected in Terraform.

---

## Stage 3: Tests

**Job:** `test`  
**Runs:** After all three parallel security analysis jobs pass  
**Blocks on:** Any test failure, coverage below 80%  

**Why the security gates run before tests:**  
If a secret is committed or a critical CVE is introduced, we don't want to waste runner minutes running tests. Security gates failing is a more fundamental problem than a test failing.

**Coverage gate:** 80% minimum. This is not an arbitrary threshold — it represents meaningful coverage of the authentication logic, IDOR protection, and input validation that the application's security depends on. The current coverage is 97.5%.

**Test categories:**
- Unit tests (`tests/unit/`): test security logic in isolation — JWT creation/verification, password hashing, token expiry, reserved claim protection
- Integration tests (`tests/integration/`): test the full HTTP stack — authentication flows, IDOR protection with two distinct users, user enumeration prevention, pagination limits

---

## Stage 4: Build

**Job:** `build`  
**Runs:** After tests pass  
**Blocks on:** Docker build failure  

**Multi-stage build rationale:**  
The builder stage installs gcc and other C build tools needed to compile bcrypt. The runtime stage copies only the compiled packages — no build tools, no pip, no source code outside `app/`. This reduces the attack surface and final image size.

**Cache:** Docker layer cache via GitHub Actions cache. If `requirements.txt` hasn't changed, the `pip install` layer is reused. Typical build time: 90 seconds cold, 20 seconds warm.

---

## Stage 5a: Image scan

**Job:** `image-scan`  
**Runs:** After build (parallel with sbom-sign)  
**Blocks on:** CRITICAL CVEs in the container image  

**Why separate from SCA:**  
SCA scans Python packages declared in `requirements.txt`. Image scan covers the OS-level packages installed in the base image (`python:3.12-slim-bookworm`). These are entirely separate CVE surfaces. A Debian package (openssl, zlib, curl) could have a critical CVE that has nothing to do with any Python dependency.

**Base image strategy:** We pin to `python:3.12-slim-bookworm`. When a new Python patch is released with security fixes, update the FROM line in the Dockerfile. The pipeline will rebuild and rescan automatically.

---

## Stage 5b: SBOM + Image signing

**Job:** `sbom-sign`  
**Runs:** After build (parallel with image-scan)  
**Blocks on:** Signing failure  

### SBOM generation (Syft)

**Why:** A Software Bill of Materials is a complete inventory of every component in the image. When a new CVE is published (e.g. a vulnerability in `python-jose`), you can query the SBOM to know immediately whether your deployed images are affected — without rebuilding.

**Format:** SPDX JSON (one of two industry standard formats alongside CycloneDX).

**Attachment:** The SBOM is attached to the image in GHCR as a Cosign attestation. It travels with the image.

### Image signing (Cosign keyless)

**Why:** Anyone with push access to GHCR could push a malicious image with a legitimate tag. Signing proves that the image was produced by the legitimate pipeline. A Kubernetes admission policy (future addition) can reject images that aren't signed by the expected identity.

**Keyless signing:** No private key to manage. Cosign requests a short-lived certificate from Sigstore's Fulcio CA, which is bound to the GitHub Actions OIDC identity (`github.com/Mbitajeff/application-delivery-pipeline@refs/heads/main`). The certificate and signature are recorded in Sigstore's Rekor transparency log — public, immutable, auditable.

**Verification command:**
```bash
cosign verify \
  --certificate-identity-regexp="https://github.com/Mbitajeff/application-delivery-pipeline/.github/workflows/pipeline.yml" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
  ghcr.io/mbitajeff/application-delivery-pipeline:sha-abc1234
```

---

## Stage 6: Push

**Job:** Push to GHCR is embedded in `sbom-sign` to ensure the image is signed before being publicly available.

**Tags:** Two tags are applied:
- `sha-<short-commit-hash>`: immutable, used for deployments
- `main`: floating tag pointing to the latest main build (used by scheduled rescans)

---

## Stage 7: DAST

**Job:** `dast`  
**Runs:** After image-scan and sbom-sign both pass  
**Current mode:** Warn-only (`continue-on-error: true`)  

**Why warn-only initially:** DAST generates noise before a baseline is established. The first scan will likely report missing security headers and information disclosure that are infrastructure concerns (Ingress config, CDN, WAF) rather than application code issues. Once a baseline scan produces zero false positives, the gate is promoted to blocking.

**What DAST finds that SAST cannot:**
- Security headers missing at the HTTP layer (not visible in source code)
- Authentication weaknesses in the actual running application
- Session management issues
- Information disclosure in error responses
- Issues that only manifest at runtime with real HTTP traffic

**Promotion path:** When ready to block:
1. Change `continue-on-error: true` to `continue-on-error: false` in `pipeline.yml`
2. Update `security/zap-config.yaml` rule thresholds from `WARN` to `FAIL` for specific rules
3. Test that the pipeline blocks correctly on a known finding

---

## Stage 8: Deploy gate

**Job:** `deploy-gate`  
**Runs:** After DAST  
**Blocks on:** No approval from required reviewer  

**Configuration:** Create a GitHub Environment named `production` in repository Settings → Environments. Add required reviewers (users who must approve before deployment proceeds).

**What this gate provides:**
- A human confirms: "I am aware this build is about to be deployed to production"
- An audit trail of who approved what, when
- A point to stop if something was noticed during the pipeline run that the automated gates didn't catch

---

## Scheduled Scan

**Workflow:** `scheduled-scan.yml`  
**Runs:** Every Monday at 06:00 UTC  

**Why:** A container image that passed all scans on build day may have new CVEs disclosed a week later. The build pipeline is not sufficient — ongoing surveillance of published images is required.

**What it scans:**
- The `:main` tag of the published image (latest main branch build)
- The dependency filesystem (latest source code on main)

**Output:** Findings uploaded to GitHub Security tab. No pipeline blocking — this is monitoring, not a gate.

---

## Adding a New Security Gate

When adding a new security tool to the pipeline:

1. Run it locally first and understand its output
2. Start with `continue-on-error: true` for two weeks
3. Review all findings — triage real vs false positive
4. Document exceptions in the appropriate config file
5. Promote to blocking once false positive rate is acceptable
6. Add a section to this document
7. Add remediation steps to `security/VULNERABILITY_REMEDIATION.md`

