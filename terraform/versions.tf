terraform {
  # S3 backend のネイティブロック(use_lockfile)を使うため 1.10 以上
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.14"
    }
  }
}
