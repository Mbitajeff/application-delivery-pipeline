# Cloud Environment Extension

This directory is a placeholder for cloud provider deployments.

## To add AWS deployment

1. Create `environments/cloud/aws/` with the same module calls as `environments/local/main.tf`
2. Remove the mock provider flags (`skip_credentials_validation` etc.)
3. Configure the S3 backend:

```hcl
backend "s3" {
  bucket         = "your-terraform-state-bucket"
  key            = "secure-pipeline/production/terraform.tfstate"
  region         = "us-east-1"
  dynamodb_table = "terraform-state-lock"
  encrypt        = true
}
```

4. Set up GitHub OIDC → AWS IAM role trust (see docs/CLOUD_EXTENSION.md)
5. Run via GitHub Actions — do not run `tofu apply` from a developer machine against production

## To add Azure or GCP

Same pattern — add `environments/cloud/azure/` or `environments/cloud/gcp/` with the
appropriate provider. The modules themselves do not change.
