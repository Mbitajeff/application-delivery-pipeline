# Operational Runbook

Day-to-day procedures for operating and maintaining the secure pipeline.

---

## First-time Setup

### 1. Clone and install

```bash
git clone https://github.com/OWNER/secure-pipeline-api.git
cd secure-pipeline-api
pip install -e ".[dev]"
```

### 2. Install pre-commit hooks

```bash
pre-commit install
# Hooks now run automatically on git commit
```

### 3. Verify everything works

```bash
make test          # 93 tests, 97%+ coverage
make lint          # Ruff — should be clean
make scan-sca      # Trivy — requires trivy to be installed
make scan-iac      # Checkov — requires checkov to be installed
```

### 4. Configure GitHub repository (one-time, requires repo admin)

```
Settings → Branches → main:
  ✓ Require pull request before merging
  ✓ Require status checks to pass (select: secret-scan, lint, test)
  ✓ Require branches to be up to date

Settings → Environments → Create "production":
  ✓ Required reviewers: [add team members]
  ✓ Deployment branches: main only

Settings → Security → Secret scanning: Enabled
Settings → Security → Dependabot alerts: Enabled
```

---

## Daily Operations

### Check pipeline status

```bash
# View last pipeline run
gh run list --limit 5

# View a specific run
gh run view RUN_ID

# Watch a running pipeline
gh run watch
```

### Review security findings

GitHub Security tab → Code scanning alerts  
Review weekly. All open alerts must be triaged (fixed or documented as accepted risk).

### View SBOM for a release

```bash
# Install cosign
# Then:
cosign verify-attestation \
  --type spdxjson \
  --certificate-identity-regexp="github.com/Mbitajeff/application-delivery-pipeline" \
  --certificate-oidc-issuer="https://token.actions.githubusercontent.com" \
  ghcr.io/mbitajeff/application-delivery-pipeline:sha-COMMIT \
  | jq '.payload | @base64d | fromjson | .predicate'
```

---

## Common Tasks

### Update a dependency

```bash
# 1. Update the version in app/requirements.txt
# 2. Test locally
pip install -r app/requirements.txt
python -m pytest

# 3. Scan for new CVEs
trivy fs . --scanners vuln --severity CRITICAL,HIGH

# 4. Commit and push — pipeline will rescan
git add app/requirements.txt
git commit -m "chore: update PACKAGE to VERSION"
```

### Add a new environment variable / configuration

```bash
# 1. Add to app/core/config.py Settings class
# 2. Add default value or mark as required
# 3. Add to infrastructure/kubernetes/configmap.yaml (if non-secret)
#    OR document that it must come from a Kubernetes Secret (if sensitive)
# 4. Update docker-compose.yml for local testing
# 5. Add test coverage for the new setting
```

### Roll back a deployment

```bash
# View deployment history
kubectl rollout history deployment/secure-pipeline-api -n secure-pipeline

# Roll back to previous revision
kubectl rollout undo deployment/secure-pipeline-api -n secure-pipeline

# Roll back to a specific revision
kubectl rollout undo deployment/secure-pipeline-api -n secure-pipeline --to-revision=N

# Verify rollback
kubectl rollout status deployment/secure-pipeline-api -n secure-pipeline
kubectl get pods -n secure-pipeline
```

### Force a full pipeline run

```bash
# From GitHub CLI
gh workflow run pipeline.yml

# Or push an empty commit
git commit --allow-empty -m "ci: trigger pipeline"
git push
```

---

## Incident Procedures

### Pipeline is blocked on a false positive

```bash
# 1. Identify the specific finding
# 2. Confirm it is not exploitable in this context
# 3. Add documented exception to the appropriate config file:
#    - Gitleaks: security/.gitleaks.toml allowlist
#    - Trivy: security/.trivyignore
#    - Checkov: security/checkov-config.yaml skip-check
#    - Semgrep: # nosemgrep: rule-id — reason: ...

# 4. The exception must include:
#    - The specific rule/CVE being suppressed
#    - Why it is not exploitable or applicable
#    - A review date (maximum 90 days)

# Never suppress a finding without a written justification.
```

### Kubernetes pod not starting

```bash
kubectl describe pod POD_NAME -n secure-pipeline
kubectl logs POD_NAME -n secure-pipeline --previous

# Common causes:
# - ImagePullBackOff: check GHCR credentials and image name
# - CrashLoopBackOff: check application logs above
# - Pending: check node resources (kubectl describe node)
# - OOMKilled: increase memory limit in deployment.yaml
```

### Application returning 500 errors

```bash
# Check application logs
kubectl logs -l app=secure-pipeline-api -n secure-pipeline --tail=100

# Check recent deployments
kubectl rollout history deployment/secure-pipeline-api -n secure-pipeline

# If caused by bad deployment: roll back (see Roll back section above)
```

---

## Maintenance Schedule

| Task | Frequency | Owner |
|------|-----------|-------|
| Review security alerts (GitHub Security tab) | Weekly | On-call |
| Review weekly scheduled scan results | Weekly | On-call |
| Update base Docker image | Monthly | Team |
| Review Checkov skip list | Quarterly | Team |
| Review threat model | Quarterly | Team |
| Update pinned action SHAs | Quarterly | Team |
| Rotate JWT secret | Annually or on suspected compromise | Security |

