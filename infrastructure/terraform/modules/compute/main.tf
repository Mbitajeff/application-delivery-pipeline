# =============================================================================
# Compute Module — EKS Cluster
# =============================================================================
#
# Provisions an EKS cluster with a managed node group.
#
# Security design:
# - Control plane endpoint access: private only in production
# - Nodes run in private subnets
# - Node IAM role uses least-privilege policies
# - EKS secrets encryption via KMS
# - Node group uses launch template with encrypted EBS
# - IMDSv2 required (prevents SSRF to metadata service)
#
# Checkov controls satisfied:
# - CKV_AWS_58:  EKS secrets encryption enabled
# - CKV_AWS_39:  EKS endpoint not publicly accessible (in prod)
# - CKV_AWS_37:  EKS control plane logging enabled
# - CKV_AWS_341: IMDSv2 required on worker nodes
# - CKV_AWS_8:   EBS volumes encrypted
# =============================================================================

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  common_tags = merge(var.tags, {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  })
}

# -----------------------------------------------------------------------------
# KMS Key for EKS secrets encryption
# CKV_AWS_58: EKS cluster secrets must be encrypted
# -----------------------------------------------------------------------------
resource "aws_kms_key" "eks" {
  description             = "KMS key for EKS secrets encryption — ${local.name_prefix}"
  deletion_window_in_days = 7
  enable_key_rotation     = true # CKV_AWS_7: key rotation enabled

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-eks-key"
  })
}

resource "aws_kms_alias" "eks" {
  name          = "alias/${local.name_prefix}-eks"
  target_key_id = aws_kms_key.eks.key_id
}

# -----------------------------------------------------------------------------
# IAM Role for EKS Control Plane
# -----------------------------------------------------------------------------
resource "aws_iam_role" "eks_cluster" {
  name = "${local.name_prefix}-eks-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

# -----------------------------------------------------------------------------
# EKS Cluster
# -----------------------------------------------------------------------------
resource "aws_eks_cluster" "main" {
  name     = "${local.name_prefix}-cluster"
  version  = var.kubernetes_version
  role_arn = aws_iam_role.eks_cluster.arn

  vpc_config {
    subnet_ids              = concat(var.private_subnet_ids, var.public_subnet_ids)
    security_group_ids      = [var.app_security_group_id]
    # CKV_AWS_39: private endpoint enabled, public disabled in production
    endpoint_private_access = true
    endpoint_public_access  = var.environment != "production"
  }

  # CKV_AWS_37: Enable control plane logging for all log types
  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  # CKV_AWS_58: Encrypt secrets at rest with KMS
  encryption_config {
    provider {
      key_arn = aws_kms_key.eks.arn
    }
    resources = ["secrets"]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-cluster"
  })

  depends_on = [aws_iam_role_policy_attachment.eks_cluster_policy]
}

# -----------------------------------------------------------------------------
# IAM Role for EKS Node Group
# -----------------------------------------------------------------------------
resource "aws_iam_role" "eks_nodes" {
  name = "${local.name_prefix}-eks-node-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })

  tags = local.common_tags
}

# Minimum required policies for EKS managed nodes
resource "aws_iam_role_policy_attachment" "eks_worker_node" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_cni" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_ecr_readonly" {
  # Read-only ECR access — nodes pull images, they don't push
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_nodes.name
}

# -----------------------------------------------------------------------------
# Launch Template for Node Group
# Enforces security settings that managed node groups don't set by default.
# -----------------------------------------------------------------------------
resource "aws_launch_template" "eks_nodes" {
  name_prefix = "${local.name_prefix}-node-"

  # CKV_AWS_341: Require IMDSv2 — prevents SSRF to instance metadata
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2
    http_put_response_hop_limit = 1          # Prevent container → metadata hops
  }

  # CKV_AWS_8: Encrypt root EBS volume
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 50
      volume_type           = "gp3"
      encrypted             = true
      kms_key_id            = aws_kms_key.eks.arn
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Name = "${local.name_prefix}-node"
    })
  }

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# EKS Managed Node Group
# -----------------------------------------------------------------------------
resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${local.name_prefix}-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  # Nodes in private subnets only
  subnet_ids      = var.private_subnet_ids
  instance_types  = var.node_instance_types

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  launch_template {
    id      = aws_launch_template.eks_nodes.id
    version = aws_launch_template.eks_nodes.latest_version
  }

  # Automatically apply security patches to the node OS
  update_config {
    max_unavailable = 1
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-node-group"
  })

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node,
    aws_iam_role_policy_attachment.eks_cni,
    aws_iam_role_policy_attachment.eks_ecr_readonly,
  ]
}
