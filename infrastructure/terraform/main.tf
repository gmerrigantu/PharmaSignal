# PharmaSignal — minimal, least-privilege AWS infrastructure (requirements §15.3).
#
# Provisions exactly what the lakehouse needs and nothing always-on:
#   - one S3 bucket for bronze/silver/gold data (+ lifecycle for temp artifacts)
#   - Glue catalog database
#   - Athena workgroup with a per-query data-scanned guardrail
#   - a read-only dashboard role and a write role for the ingestion pipeline
#   - optional Secrets Manager entry for API keys
#
# This is intentionally simple and OPTIONAL for the MVP. See ../CLOUD_SETUP.md.
#
#   terraform init && terraform apply -var="bucket_name=pharmasignal-data-<unique>"

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "bucket_name" {
  type        = string
  description = "Globally-unique S3 bucket name for the lakehouse."
}

variable "max_query_bytes_scanned" {
  type        = number
  default     = 1073741824 # 1 GiB per-query guardrail
  description = "Athena per-query data-scanned limit (cost control)."
}

# --------------------------------------------------------------------------- #
# Data lake bucket
# --------------------------------------------------------------------------- #
resource "aws_s3_bucket" "lake" {
  bucket = var.bucket_name
  tags   = { Project = "PharmaSignal", ManagedBy = "Terraform" }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Expire temporary / Athena-results artifacts; keep curated layers.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    id     = "expire-tmp-and-athena-results"
    status = "Enabled"
    filter { prefix = "tmp/" }
    expiration { days = 7 }
  }
  rule {
    id     = "expire-athena-results"
    status = "Enabled"
    filter { prefix = "athena-results/" }
    expiration { days = 14 }
  }
}

# --------------------------------------------------------------------------- #
# Glue catalog + Athena workgroup
# --------------------------------------------------------------------------- #
resource "aws_glue_catalog_database" "pharmasignal" {
  name = "pharmasignal"
}

resource "aws_athena_workgroup" "pharmasignal" {
  name = "pharmasignal"
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.max_query_bytes_scanned
    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"
    }
  }
}

# --------------------------------------------------------------------------- #
# Least-privilege IAM: read-only dashboard role + write ingestion role
# --------------------------------------------------------------------------- #
data "aws_iam_policy_document" "dashboard_read" {
  statement {
    sid       = "ReadGold"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/gold/*"]
  }
  statement {
    sid       = "AthenaQuery"
    actions   = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "glue:GetTable", "glue:GetDatabase", "glue:GetPartitions"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "dashboard_read" {
  name   = "pharmasignal-dashboard-read"
  policy = data.aws_iam_policy_document.dashboard_read.json
}

data "aws_iam_policy_document" "pipeline_write" {
  statement {
    sid       = "WriteLake"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    sid       = "CatalogWrite"
    actions   = ["glue:CreateTable", "glue:UpdateTable", "glue:GetTable", "glue:GetDatabase", "glue:BatchCreatePartition"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "pipeline_write" {
  name   = "pharmasignal-pipeline-write"
  policy = data.aws_iam_policy_document.pipeline_write.json
}

# Attach these policies to whichever principal runs the dashboard / pipeline
# (e.g. a GitHub Actions OIDC role, an EC2 instance profile, or an IAM user).

# --------------------------------------------------------------------------- #
# Optional: secret for an API key (never store secrets in code)
# --------------------------------------------------------------------------- #
resource "aws_secretsmanager_secret" "api_keys" {
  name                    = "pharmasignal/api-keys"
  recovery_window_in_days = 0
}

output "bucket" { value = aws_s3_bucket.lake.bucket }
output "glue_database" { value = aws_glue_catalog_database.pharmasignal.name }
output "athena_workgroup" { value = aws_athena_workgroup.pharmasignal.name }
output "dashboard_read_policy_arn" { value = aws_iam_policy.dashboard_read.arn }
output "pipeline_write_policy_arn" { value = aws_iam_policy.pipeline_write.arn }
