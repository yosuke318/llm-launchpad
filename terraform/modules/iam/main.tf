terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

data "aws_partition" "current" {}

resource "aws_iam_role" "instance" {
  name_prefix = var.name_prefix
  description = "GPU instance role (SSM, model bucket read)"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = var.tags
}

# SSM Session Manager(シェル接続とポートフォワード)に必要
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# 起動処理の進み具合を、その環境のパラメータにだけ書ける
resource "aws_iam_role_policy" "status_parameter_write" {
  name = "status-parameter-write"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "WriteBootstrapStatus"
      Effect   = "Allow"
      Action   = ["ssm:PutParameter", "ssm:GetParameter"]
      Resource = var.status_parameter_arn
    }]
  })
}

# S3キャッシュ。ollama/ の下のモデルを読み、なかったモデルを保存する
resource "aws_iam_role_policy" "model_bucket" {
  count = var.model_bucket_name == null ? 0 : 1

  name = "model-bucket"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ListBucket がないと、無いオブジェクトの head-object が 404 ではなく 403 になる
        Sid      = "ListModelBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.model_bucket_name}"
        Condition = {
          StringLike = { "s3:prefix" = ["ollama/*"] }
        }
      },
      {
        Sid      = "ReadWriteModelObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
        Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.model_bucket_name}/ollama/*"
      },
    ]
  })
}

# 閉じたネットワーク。ollama.com から入れられないため、バケットに置いた Ollama のリリースファイルを読む
resource "aws_iam_role_policy" "ollama_install" {
  count = var.ollama_install_bucket_name == null ? 0 : 1

  name = "ollama-install"
  role = aws_iam_role.instance.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadOllamaRelease"
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "arn:${data.aws_partition.current.partition}:s3:::${var.ollama_install_bucket_name}/ollama/install/*"
    }]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name_prefix = var.name_prefix
  role        = aws_iam_role.instance.name

  tags = var.tags
}
