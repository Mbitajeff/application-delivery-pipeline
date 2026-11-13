# =============================================================================
# Local environment — no cloud credentials required
# =============================================================================
#
# This environment uses the AWS provider configured to use a mock/local
# endpoint. The purpose is to give Checkov real HCL to scan.
#
# `tofu validate` will succeed because the HCL is syntactically valid.
# `tofu plan` or `tofu apply` would require real AWS credentials.
#
# When cloud credentials become available:
#   1. Copy this directory to environments/cloud/aws/
#   2. Remove the mock_provider override
#   3. Set real values in terraform.tfvars
#   4. Configure remote backend (S3 + DynamoDB)
# =============================================================================

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Local filesystem backend — no remote state needed for scanning.
  # For cloud deployment: replace with S3 backend.
  backend "local" {
    path = "terraform.tfstate"
  }
}

# Provider configured for local/offline use.
# Checkov scans the HCL, not the provider — this is intentional.
provider "aws" {
  region = var.aws_region

  # These allow tofu validate to pass without real credentials.
  # Remove for real deployments.
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# -----------------------------------------------------------------------------
# Networking
# -----------------------------------------------------------------------------
module "networking" {
  source = "../../modules/networking"

  project_name         = var.project_name
  environment          = var.environment
  vpc_cidr             = var.vpc_cidr
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  availability_zones   = var.availability_zones
  app_port             = var.app_port
}

# -----------------------------------------------------------------------------
# Compute
# -----------------------------------------------------------------------------
module "compute" {
  source = "../../modules/compute"

  project_name          = var.project_name
  environment           = var.environment
  vpc_id                = module.networking.vpc_id
  private_subnet_ids    = module.networking.private_subnet_ids
  public_subnet_ids     = module.networking.public_subnet_ids
  app_security_group_id = module.networking.app_security_group_id
  kubernetes_version    = var.kubernetes_version
  node_instance_types   = var.node_instance_types
  node_desired_size     = var.node_desired_size
  node_min_size         = var.node_min_size
  node_max_size         = var.node_max_size
}
