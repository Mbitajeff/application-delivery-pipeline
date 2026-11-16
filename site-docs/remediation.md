# Vulnerability Remediation Runbook

**Purpose:** Step-by-step response procedures for every security gate failure in the pipeline.  
**When to use:** A pipeline stage fails, a scheduled scan finds new CVEs, or a security incident is declared.

---

## General Principle

For every finding, work through this sequence:

```
1. Confirm it is real (not a false positive)
2. Assess impact and exploitability in your context
3. Fix or document a justified exception
4. Verify the fix passes the gate
5. Update this document if the response pattern was new
```

---

## Gate 1: Gitleaks — Secret Scanning

### Pipeline blocks with: `Secret detected`

**Step 1 — Identify the finding**
```bash
# Run locally to see the exact finding
gitleaks detect --source . --config security/.gitleaks.toml --verbose
```

**Step 2 — Is it a real secret?**
- If yes: the secret is compromised. Treat it as exposed even if no one saw it. Rotate immediately.
- If no (test value, example, false positive): add to `security/.gitleaks.toml` allowlist.

**Step 3 — If the secret is real, rotate it now**
```bash
# Example: leaked SECRET_KEY
# 1. Generate a new secret
python -c "import secrets; print(secrets.token_hex(32))"

# 2. Update the secret in your secrets manager (not in the code)
# AWS Secrets Manager:
aws secretsmanager update-secret \
  --secret-id secure-pipeline/secret-key \
  --secret-string "NEW_SECRET_VALUE"

# 3. Redeploy to pick up the new secret
# 4. All existing JWT tokens are now invalid — users must re-login
```

**Step 4 — Remove from git history**
```bash
# After rotating, remove from history using git-filter-repo
pip install git-filter-repo
git filter-repo --path app/core/config.py --force
# Force push requires explicit approval — coordinate with the team
```

**Step 5 — Prevent recurrence**
- Add pre-commit hook locally: `pre-commit install`
- Verify `.gitleaks.toml` allowlist does not over-permit

---

## Gate 2: Semgrep — SAST

### Pipeline blocks with: `Found X findings at or above ERROR severity`

**Step 1 — View the findings**
The SARIF report is uploaded to the GitHub Security tab. For local investigation:
```bash
semgrep scan --config security/.semgrep.yml app/ --verbose
```

**Step 2 — Triage each finding**

| Finding type | Response |
|--------------|----------|
| Hardcoded secret | Rotate and remove (see Gate 1) |
| SQL injection | Replace string interpolation with parameterised queries |
| Insecure JWT usage | Review `app/core/security.py`; check algorithm and expiry |
| Open redirect | Add URL allowlist validation |
| Debug mode enabled | Use `Settings.debug` from environment, never hardcode |

**Step 3 — If the finding is a false positive**
```python
# Add a nosemgrep comment on the specific line with justification
result = some_safe_operation()  # nosemgrep: rule-id — reason: ...
```
Document false positives in a comment. Never use `nosemgrep` without a reason.

---

## Gate 3: Trivy SCA — Dependency Vulnerabilities

### Pipeline blocks with: `Found X vulnerabilities in CRITICAL/HIGH severity`

**Step 1 — View findings locally**
```bash
trivy fs . --scanners vuln --severity CRITICAL,HIGH
```

**Step 2 — For each finding**

```
CVE-XXXX-YYYY in package==version
```

Check:
1. Is there a patched version? → Upgrade in `app/requirements.txt`
2. Is the vulnerability in a code path your app uses? → If not, document as not exploitable
3. Is there no fix yet? → Document in `security/.trivyignore` with CVE ID and justification

**Step 3 — Upgrade the dependency**
```bash
# Check what version fixes the CVE
pip index versions package-name

# Update requirements.txt
# Replace: package==old-version
# With:    package==fixed-version

# Verify the fix
pip install -r app/requirements.txt
python -m pytest  # Confirm nothing broke
trivy fs . --scanners vuln --severity CRITICAL,HIGH  # Confirm CVE is gone
```

**Step 4 — If no fix is available**
```bash
# Create security/.trivyignore
# Format:
# CVE-XXXX-YYYY
# # Justification: package X version Y has no patch available as of DATE.
# # The vulnerable code path (feature Z) is not used by this application.
# # Review date: DATE + 30 days
```

**Never suppress a CVE without a written justification and review date.**

---

## Gate 4: Checkov — IaC Scanning

### Pipeline blocks with: `Check: "RULE_NAME" FAILED`

**Step 1 — Understand the rule**
```bash
checkov -d infrastructure/ --list  # Shows all available rules
checkov --check CKV_AWS_XXX --guide  # Shows remediation guide for specific rule
```

**Step 2 — Fix the misconfiguration**

Common fixes:

| Check | Fix |
|-------|-----|
| CKV_AWS_25: SSH from 0.0.0.0/0 | Remove port 22 ingress rule entirely; use SSM Session Manager |
| CKV_AWS_130: Subnet auto-assigns public IPs | Set `map_public_ip_on_launch = false` |
| CKV_AWS_58: EKS secrets not encrypted | Add `encryption_config` block with KMS key |
| CKV_K8S_20: Container runs as root | Add `runAsNonRoot: true` and `runAsUser: 1000` |
| CKV_K8S_22: No read-only root filesystem | Add `readOnlyRootFilesystem: true`; add tmpfs for writable dirs |
| CKV_K8S_30: Capabilities not dropped | Add `capabilities: drop: [ALL]` |

**Step 3 — If the rule must be skipped**
Add to `security/checkov-config.yaml` with justification:
```yaml
skip-check:
  - CKV_AWS_XXX  # Justification: specific reason why this doesn't apply
                  # Review date: DATE
```

Rules with `# It fails` as the justification will be rejected in code review.

---

## Gate 5: Tests + Coverage

### Pipeline blocks with: `FAILED` or `Required test coverage X% not reached`

**Test failure:**
```bash
python -m pytest --no-cov -x --tb=long  # Stop at first failure, full traceback
```
Fix the failing test or the application code. Do not delete tests to make coverage pass.

**Coverage below 80%:**
```bash
python -m pytest --cov-report=term-missing  # Shows exactly which lines are uncovered
```
Add tests for uncovered lines. Do not use `# pragma: no cover` without a written reason.

---

## Gate 6: Trivy Image Scan

### Pipeline blocks with: `Found X vulnerabilities at CRITICAL severity in image`

This is separate from SCA (Gate 3) — this scans the OS packages in the container image, not Python dependencies.

**Step 1 — Identify which OS package has the CVE**
```bash
trivy image --severity CRITICAL secure-pipeline-api:dev
```

**Step 2 — Update the base image**
In `Dockerfile`, change the base image to a newer patch:
```dockerfile
# Before
FROM python:3.12-slim-bookworm AS builder

# After — if a newer patch is available
FROM python:3.12.X-slim-bookworm AS builder  # Use specific patch version
```

**Step 3 — If the base image has no patch yet**
Check if the CVE is actually reachable from your application. Many OS-level CVEs affect packages that are not installed or not reachable from your code path. Document in a `.trivyignore` for the image scan with the same justification format as Gate 3.

---

## Gate 7: SBOM + Image Signing

### Pipeline fails: `cosign sign failed`

```bash
# Check OIDC token is available (only works in GitHub Actions)
# Locally: use a key pair for testing
cosign generate-key-pair  # Creates cosign.key and cosign.pub

# Sign with key pair locally
cosign sign --key cosign.key IMAGE:TAG
```

If this fails in CI: check that `id-token: write` permission is set on the `sbom-sign` job.

---

## Gate 8: DAST (OWASP ZAP)

### ZAP reports findings (currently warn-only)

**Step 1 — Get the report**
Download the ZAP report artifact from the GitHub Actions run.

**Step 2 — Triage findings by risk**

| ZAP alert type | Typical fix |
|----------------|-------------|
| Missing HSTS header | Configured in Ingress annotation — verify deployment |
| Missing CSP header | Add to Ingress `configuration-snippet` |
| X-Content-Type-Options missing | Add to Ingress headers |
| Information disclosure (server header) | Add `nginx.ingress.kubernetes.io/server-snippet` to hide version |
| Authentication issues | Review JWT expiry, missing auth on endpoints |
| Session token in URL | Never pass tokens as query parameters |

**Step 3 — When to promote from WARN to FAIL**
Once a ZAP baseline run produces zero FAIL-level findings, change `continue-on-error: true` to `continue-on-error: false` in the pipeline DAST job.

---

## Security Incident Response

### Leaked AWS credential (access key)

```
1. Deactivate the key IMMEDIATELY via AWS Console or CLI
   aws iam update-access-key --access-key-id AKID --status Inactive

2. Check CloudTrail for unauthorized API calls in the last 24 hours
   aws cloudtrail lookup-events \
     --lookup-attributes AttributeKey=AccessKeyId,AttributeValue=AKID

3. Create a new key and update secrets manager
4. Rotate any resources that used that key
5. Add Gitleaks rule if the key pattern wasn't detected
6. Post-mortem: how did the key leave the system?
```

### Compromised container image

```
1. Revoke the image in GHCR (delete the specific tag)
2. Check the Cosign signature — was it signed by the legitimate pipeline?
   cosign verify --certificate-identity-regexp="github.com/Mbitajeff/application-delivery-pipeline"
                 --certificate-oidc-issuer="https://token.actions.githubusercontent.com"
                 IMAGE@DIGEST
3. Check all running clusters for the compromised image
   kubectl get pods -A -o jsonpath='{range .items[*]}{.spec.containers[*].image}{"\n"}{end}' | grep DIGEST
4. Roll back to the last verified good image
5. Investigate how a compromised image was produced
```

### Secret accidentally committed to git

```
1. Rotate the secret IMMEDIATELY — assume it is compromised
2. Remove from git history (see Gate 1 remediation above)
3. Add to Gitleaks allowlist only AFTER confirming it's been rotated
   and the old value is dead
4. Add pre-commit hook if not already installed
5. Consider adding a GitHub Secret Scanning push protection rule
```

