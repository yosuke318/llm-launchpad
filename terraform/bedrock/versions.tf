terraform {
  # S3 backend のネイティブロック(use_lockfile)を使うため 1.10 以上
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  # 消し忘れ検出で使う管理用タグ。全リソースに付く
  default_tags {
    tags = local.default_tags
  }
}
