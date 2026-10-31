# Bedrock の環境。GPUインスタンスは起動せず、インターネットへの経路がない VPC に Lambda を置いて、
# bedrock-runtime のインターフェイスエンドポイント(PrivateLink)経由でモデルを呼ぶ。
# GPUインスタンスの環境(terraform/)とは別の state で、workspace ごとに環境を分ける

resource "time_static" "created" {
  lifecycle {
    precondition {
      condition     = can(regex("^[a-z0-9][a-z0-9-]{0,19}$", local.env_id))
      error_message = "env_id(未指定なら workspace 名)は、英小文字・数字・ハイフンの20文字以内にしてください。"
    }
  }
}

locals {
  project = "llm-launchpad"
  env_id  = coalesce(var.env_id, terraform.workspace)
  name    = "${local.project}-${local.env_id}"

  # IAM の name_prefix は38文字までのため短い接頭辞を使う
  iam_name_prefix = "llm-lp-${local.env_id}-"

  # 管理用タグは extra_tags で上書きできないよう後ろに置く
  default_tags = merge(var.extra_tags, {
    Project   = local.project
    EnvId     = local.env_id
    ManagedBy = "terraform"
    Engine    = "bedrock"
  })

  resource_tags = {
    CreatedAt = time_static.created.rfc3339
  }

  catalog = yamldecode(file("${path.module}/../../catalog/bedrock_models.yaml")).models
  model   = one([for m in local.catalog : m if m.id == var.model])

  # エンドポイントを置けるAZのうち、指定がなければ名前順で最初のもの
  endpoint_azs      = sort(setintersection(data.aws_availability_zones.available.names, data.aws_vpc_endpoint_service.bedrock_runtime.availability_zones))
  availability_zone = coalesce(var.availability_zone, try(local.endpoint_azs[0], "-"))
}

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_vpc_endpoint_service" "bedrock_runtime" {
  service      = "bedrock-runtime"
  service_type = "Interface"
}

# 推論プロファイルが振り分ける先のモデル(jp. なら東京と大阪)。IAM でこれらを許可する
data "aws_bedrock_inference_profile" "this" {
  inference_profile_id = var.model

  lifecycle {
    precondition {
      condition     = local.model != null
      error_message = "model(${var.model})が catalog/bedrock_models.yaml にありません。"
    }
  }
}

# インターネットゲートウェイのない VPC。bedrock-runtime のエンドポイントだけを置く
module "network" {
  source = "../modules/network"

  name                        = local.name
  region                      = var.region
  vpc_cidr                    = var.vpc_cidr
  az_count                    = 3
  private                     = true
  interface_endpoint_services = ["bedrock-runtime"]
  endpoint_availability_zone  = local.availability_zone
  tags                        = local.resource_tags
}

# Lambda は外向きに VPC の中の HTTPS(エンドポイント)にだけ出られる
resource "aws_security_group" "lambda" {
  name_prefix = "${local.name}-lambda-"
  description = "Lambda that calls Bedrock for ${local.name}"
  vpc_id      = module.network.vpc_id

  tags = merge(local.resource_tags, { Name = "${local.name}-lambda" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "lambda_https" {
  security_group_id = aws_security_group.lambda.id
  description       = "HTTPS to the VPC endpoints"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = var.vpc_cidr

  tags = merge(local.resource_tags, { Name = "${local.name}-lambda-https" })
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name_prefix        = local.iam_name_prefix
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json

  tags = local.resource_tags
}

# VPC の中に ENI を作るための権限と、ログの書き込み
resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# 呼べるのは選んだ推論プロファイルだけ。推論プロファイルは、この VPC のエンドポイントを通ったときだけ呼べる。
# 振り分け先のモデルは、その推論プロファイルを通したときだけ呼べる
data "aws_iam_policy_document" "bedrock" {
  statement {
    sid       = "InvokeInferenceProfile"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [data.aws_bedrock_inference_profile.this.inference_profile_arn]

    condition {
      test     = "StringEquals"
      variable = "aws:SourceVpce"
      values   = module.network.vpc_endpoint_ids
    }
  }

  statement {
    sid       = "InvokeRoutedModels"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = data.aws_bedrock_inference_profile.this.models[*].model_arn

    condition {
      test     = "StringEquals"
      variable = "bedrock:InferenceProfileArn"
      values   = [data.aws_bedrock_inference_profile.this.inference_profile_arn]
    }
  }
}

resource "aws_iam_role_policy" "bedrock" {
  name   = "bedrock-invoke"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.bedrock.json
}

# destroy で一緒に消えるよう、ロググループを先に作っておく
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${local.name}-bedrock"
  retention_in_days = var.log_retention_days

  tags = local.resource_tags
}

data "archive_file" "handler" {
  type        = "zip"
  source_file = "${path.module}/files/handler.py"
  output_path = "${path.module}/.build/handler.zip"
}

resource "aws_lambda_function" "this" {
  function_name    = "${local.name}-bedrock"
  description      = "Calls ${var.model} through the bedrock-runtime VPC endpoint"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.13"
  architectures    = ["arm64"]
  handler          = "handler.handler"
  filename         = data.archive_file.handler.output_path
  source_code_hash = data.archive_file.handler.output_base64sha256
  memory_size      = 256
  # ベンチマーク(3つのプロンプト × 最大10回)が終わるまで待てる長さ
  timeout = 900

  environment {
    variables = {
      MODEL_ID = var.model
    }
  }

  # エンドポイントと同じAZのサブネットにだけ置き、AZをまたぐ転送料金をなくす
  vpc_config {
    subnet_ids         = [module.network.subnet_ids[local.availability_zone]]
    security_group_ids = [aws_security_group.lambda.id]
  }

  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda.name
  }

  tags = local.resource_tags

  depends_on = [aws_iam_role_policy_attachment.lambda_vpc, aws_iam_role_policy.bedrock]
}
