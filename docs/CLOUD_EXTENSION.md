# Cloud Extension Guide

**Purpose:** How to extend this project to deploy to AWS, Azure, or GCP without rewriting the application or core pipeline.

The application, security gates, SBOM generation, and image signing are all cloud-agnostic. Only four things change when adding a cloud provider:

1. Container registry (GHCR → ECR/ACR/GAR)
2. Kubernetes runtime (Kind → EKS/AKS/GKE)
3. Terraform provider configuration
4. OIDC trust relationship (GitHub → cloud IAM role)

---

## Prerequisites for Any Cloud Extension

Before adding any cloud provider, complete these steps:

1. **Never store long-lived cloud credentials in GitHub Secrets.** Use OIDC.
2. **Create a dedicated IAM identity** for the pipeline with least-privilege permissions.
3. **Remote Terraform state** must be encrypted at rest and locked during apply.
4. **Test in a non-production environment first.**

---

## AWS Extension

### Step 1: Set up GitHub OIDC → AWS IAM

```hcl
# In Terraform (run once manually or via a bootstrap script)

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcd"
  ]
}

resource "aws_iam_role" "github_actions_pipeline" {
  name = "github-actions-secure-pipeline"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          # IMPORTANT: Scope to your specific repo and branch
          # Never use "*" here — that allows any GitHub repo to assume this role
          "token.actions.githubusercontent.com:sub" = "repo:Mbitajeff/application-delivery-pipeline:ref:refs/heads/main"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions_pipeline" {
  role = aws_iam_role.github_actions_pipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ECR — push images
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeRepositories",
          "ecr:CreateRepository"
        ]
        Resource = "*"
      },
      {
        # EKS — update deployments
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster"
        ]
        Resource = "arn:aws:eks:REGION:ACCOUNT_ID:cluster/CLUSTER_NAME"
      }
    ]
  })
}
```

### Step 2: Add AWS authentication to pipeline.yml

```yaml
# Add to jobs that need AWS access (push, deploy):
- name: Configure AWS credentials (OIDC)
  uses: aws-actions/configure-aws-credentials@e3dd6a429d7300a6a4c196c26e071d42e0343502  # v4.0.2
  with:
    role-to-assume: arn:aws:iam::ACCOUNT_ID:role/github-actions-secure-pipeline
    aws-region: us-east-1
    # No access keys stored in GitHub Secrets
```

### Step 3: Create ECR repository

```bash
aws ecr create-repository \
  --repository-name secure-pipeline-api \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=KMS \
  --region us-east-1
```

### Step 4: Update registry references

Change three values in `pipeline.yml`:
```yaml
# Before (GHCR)
env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

# After (ECR)
env:
  REGISTRY: ACCOUNT_ID.dkr.ecr.us-east-1.amazonaws.com
  IMAGE_NAME: secure-pipeline-api
```

Replace `docker/login-action` with `aws-actions/amazon-ecr-login`:
```yaml
- name: Log in to ECR
  uses: aws-actions/amazon-ecr-login@062b18b96a7aff071d4dc91bc00c4c1a7945b076  # v2.0.1
```

### Step 5: Add Terraform state backend

```hcl
# In infrastructure/terraform/environments/cloud/aws/main.tf
terraform {
  backend "s3" {
    bucket         = "your-terraform-state-bucket"
    key            = "secure-pipeline/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "terraform-state-lock"
    encrypt        = true
  }
}
```

### Step 6: EKS deployment

```yaml
# Add to deploy-gate job in pipeline.yml
- name: Configure kubectl for EKS
  run: |
    aws eks update-kubeconfig \
      --region us-east-1 \
      --name secure-pipeline-local-cluster

- name: Deploy to EKS
  run: |
    kubectl set image deployment/secure-pipeline-api \
      api=${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:sha-${{ github.sha }} \
      -n secure-pipeline
    kubectl rollout status deployment/secure-pipeline-api -n secure-pipeline
```

---

## Azure Extension

### Step 1: GitHub OIDC → Azure federated credential

```bash
# Create a service principal
az ad app create --display-name "github-actions-secure-pipeline"
APP_ID=$(az ad app list --display-name "github-actions-secure-pipeline" --query "[0].appId" -o tsv)

# Create federated credential (replace Mbitajeff/application-delivery-pipeline)
az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-main-branch",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:Mbitajeff/application-delivery-pipeline:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

### Step 2: Add Azure auth to pipeline.yml

```yaml
- name: Azure login (OIDC)
  uses: azure/login@a65d910e8af852a8061c627c456678983e180302  # v2.2.0
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
    # No client secret — OIDC token exchange
```

### Registry and cluster

```yaml
# Registry: Azure Container Registry
REGISTRY: myregistry.azurecr.io

# Login:
- uses: azure/docker-login@15c4aadf093404726ab2ff205b2cdd33fa6d054c  # v2
  with:
    login-server: myregistry.azurecr.io

# Cluster: AKS
- uses: azure/aks-set-context@37b5b7a4b134b4a6f5f5a09b1ec4f33c77dde0ee  # v4
  with:
    resource-group: my-resource-group
    cluster-name: my-aks-cluster
```

---

## GCP Extension

### Step 1: GitHub OIDC → GCP Workload Identity Federation

```bash
# Create Workload Identity Pool
gcloud iam workload-identity-pools create "github-pool" \
  --project=PROJECT_ID \
  --location="global"

# Create provider
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --project=PROJECT_ID \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Actions" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com"

# Allow the specific repo to impersonate the service account
gcloud iam service-accounts add-iam-policy-binding "SA_EMAIL" \
  --project=PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/attribute.repository/Mbitajeff/application-delivery-pipeline"
```

### Step 2: Add GCP auth to pipeline.yml

```yaml
- name: Authenticate to GCP (OIDC)
  uses: google-github-actions/auth@62cf5bd3e4211a0a0b51f2c6d6a37129d828611d  # v2.1.7
  with:
    workload_identity_provider: projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider
    service_account: SA_EMAIL

- name: Authenticate to GAR
  uses: docker/login-action@9780b0c442fbb1117ed29e0efdff1e18412f7567  # v3.3.0
  with:
    registry: us-docker.pkg.dev
    username: oauth2accesstoken
    password: ${{ steps.auth.outputs.auth_token }}
```

---

## Multi-cloud checklist

When extending to any cloud, verify:

- [ ] OIDC trust policy is scoped to specific repo AND branch (`repo:Mbitajeff/application-delivery-pipeline:ref:refs/heads/main`)
- [ ] IAM role/service account has only the permissions needed (ECR push, EKS describe, nothing else)
- [ ] Terraform state is encrypted at rest and locked during apply
- [ ] No long-lived credentials in GitHub Secrets
- [ ] Registry images still signed with Cosign (signing is provider-agnostic)
- [ ] SBOM still generated and attached (provider-agnostic)
- [ ] Checkov still scans the cloud-specific Terraform configurations
- [ ] Application code is unchanged
- [ ] Security gates are unchanged

