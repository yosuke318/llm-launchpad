# モデルの置き場所(S3バケットとEBSボリューム)。GPUインスタンスの環境とは別の state で管理し、
# 環境を destroy しても消えないようにする。リージョンごとに1つ apply する

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "llm-launchpad"
      EnvId     = "model-store"
      ManagedBy = "terraform"
      # 環境を destroy しても残るもの。消し忘れ検出で区別する
      Persistent = "true"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  # 環境側の構成も同じ規則で名前を組み立てる(terraform/locals.tf)
  bucket_name = coalesce(var.bucket_name, "llm-launchpad-models-${data.aws_caller_identity.current.account_id}-${var.region}")
}

# A. S3キャッシュ。Ollama の models ディレクトリと同じ構成(manifests/ と blobs/)を ollama/models/ の下に置く
resource "aws_s3_bucket" "models" {
  count = var.create_bucket ? 1 : 0

  bucket = local.bucket_name
}

resource "aws_s3_bucket_public_access_block" "models" {
  count = var.create_bucket ? 1 : 0

  bucket                  = aws_s3_bucket.models[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "models" {
  count = var.create_bucket ? 1 : 0

  bucket = aws_s3_bucket.models[0].id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "models" {
  count = var.create_bucket ? 1 : 0

  bucket = aws_s3_bucket.models[0].id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 保存の途中でインスタンスが止まったときの、途中までのアップロードを消す(残ると保管料金がかかる)
resource "aws_s3_bucket_lifecycle_configuration" "models" {
  count = var.create_bucket ? 1 : 0

  bucket = aws_s3_bucket.models[0].id

  rule {
    id     = "abort-incomplete-multipart-upload"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# B. EBS永続。AZごとに作る(EBSは作ったAZのインスタンスにしか付けられない)
resource "aws_ebs_volume" "models" {
  for_each = var.model_volumes

  availability_zone = each.value.availability_zone
  size              = each.value.size_gib
  type              = "gp3"
  iops              = each.value.iops
  throughput        = each.value.throughput
  encrypted         = true

  tags = {
    Name        = "llm-launchpad-models-${each.key}"
    ModelVolume = each.key
  }
}
