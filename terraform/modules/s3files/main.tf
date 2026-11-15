# モデルの置き場所 s3_files: model-store のバケットの ollama/models/ を、Amazon S3 Files で NFS としてマウントする。
# モデルをルートボリュームにコピーせず、Ollama が S3 から直接読む(1 MiB 以上の読み込みは S3 から直接流れる)。
#
# 作るもの(環境ごと。destroy してもバケットのモデルは残る):
#   S3 Files がバケットを読み書きする IAM ロール、ファイルシステム、サブネットごとのマウントターゲット、
#   マウントターゲットのセキュリティグループと NFS(2049/tcp)の通り道、インスタンスがマウントする権限
#
# 権限と通り道は、AWS のドキュメント「Prerequisites for S3 Files」のとおり
# (https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-files-prereq-policies.html)

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    time = {
      source = "hashicorp/time"
    }
  }
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region
  bucket_arn = "arn:${local.partition}:s3:::${var.bucket_name}"
}

# S3 Files がバケットとの同期に使うロール
resource "aws_iam_role" "file_system" {
  name_prefix = "${var.name_prefix}s3files-"
  description = "S3 Files reads and writes ${var.bucket_name} for ${var.name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3FilesAssumeRole"
      Effect    = "Allow"
      Principal = { Service = "elasticfilesystem.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnLike      = { "aws:SourceArn" = "arn:${local.partition}:s3files:${local.region}:${local.account_id}:file-system/*" }
      }
    }]
  })

  tags = var.tags
}

resource "aws_iam_role_policy" "file_system" {
  name = "s3files-bucket-sync"
  role = aws_iam_role.file_system.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "S3BucketPermissions"
        Effect    = "Allow"
        Action    = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource  = local.bucket_arn
        Condition = { StringEquals = { "aws:ResourceAccount" = local.account_id } }
      },
      {
        Sid       = "S3ObjectPermissions"
        Effect    = "Allow"
        Action    = ["s3:AbortMultipartUpload", "s3:DeleteObject*", "s3:GetObject*", "s3:List*", "s3:PutObject*"]
        Resource  = "${local.bucket_arn}/*"
        Condition = { StringEquals = { "aws:ResourceAccount" = local.account_id } }
      },
      {
        Sid      = "EventBridgeManage"
        Effect   = "Allow"
        Action   = ["events:DeleteRule", "events:DisableRule", "events:EnableRule", "events:PutRule", "events:PutTargets", "events:RemoveTargets"]
        Resource = ["arn:${local.partition}:events:*:*:rule/DO-NOT-DELETE-S3-Files*"]
        Condition = {
          StringEquals = { "events:ManagedBy" = "elasticfilesystem.amazonaws.com" }
        }
      },
      {
        Sid      = "EventBridgeRead"
        Effect   = "Allow"
        Action   = ["events:DescribeRule", "events:ListRuleNamesByTarget", "events:ListRules", "events:ListTargetsByRule"]
        Resource = ["arn:${local.partition}:events:*:*:rule/*"]
      },
    ]
  })
}

# IAM のロールは作ってすぐには使えないことがある(ファイルシステムを作るときに引き受けられず失敗する)
resource "time_sleep" "role_propagation" {
  depends_on      = [aws_iam_role_policy.file_system]
  create_duration = "15s"
}

resource "aws_s3files_file_system" "this" {
  bucket   = local.bucket_arn
  prefix   = var.prefix
  role_arn = aws_iam_role.file_system.arn

  tags = merge(var.tags, { Name = var.name })

  depends_on = [time_sleep.role_propagation]
}

# マウントターゲットのセキュリティグループ。インスタンスからの NFS(2049/tcp)だけを入れる
resource "aws_security_group" "mount_target" {
  name_prefix = "${var.name}-s3files-"
  description = "S3 Files mount targets for ${var.name}"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = "${var.name}-s3files" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "nfs_from_instance" {
  security_group_id            = aws_security_group.mount_target.id
  description                  = "NFS from the GPU instance"
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  referenced_security_group_id = var.instance_security_group_id

  tags = merge(var.tags, { Name = "${var.name}-s3files-nfs-in" })
}

# インスタンスのアウトバウンドは HTTPS と HTTP だけなので、マウントターゲットへの NFS を足す
resource "aws_vpc_security_group_egress_rule" "nfs_to_mount_target" {
  security_group_id            = var.instance_security_group_id
  description                  = "NFS to the S3 Files mount targets"
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
  referenced_security_group_id = aws_security_group.mount_target.id

  tags = merge(var.tags, { Name = "${var.name}-s3files-nfs-out" })
}

# マウントターゲットは AZ ごとに1つまで。インスタンスがどの AZ に起動してもマウントできるよう、全部のサブネットに置く
resource "aws_s3files_mount_target" "this" {
  # キーは AZ 名(plan のときに決まっている)
  for_each = var.subnet_ids

  file_system_id  = aws_s3files_file_system.this.id
  subnet_id       = each.value
  security_groups = [aws_security_group.mount_target.id]
}

# インスタンスがマウントし、書き込み(モデルの取得)をする権限
resource "aws_iam_role_policy_attachment" "client" {
  role       = var.instance_role_name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonS3FilesClientFullAccess"
}

# 大きい読み込みは、マウントヘルパーがバケットから直接読む。そのための読み取りの権限(バージョン付きを含む)
resource "aws_iam_role_policy" "client_direct_read" {
  name = "s3files-direct-read"
  role = var.instance_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "S3ObjectReadAccess"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${local.bucket_arn}/${var.prefix}*"
      },
      {
        Sid      = "S3BucketListAccess"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.bucket_arn
      },
    ]
  })
}
