resource "time_static" "created" {
  lifecycle {
    precondition {
      condition     = can(regex("^[a-z0-9][a-z0-9-]{0,19}$", local.env_id))
      error_message = "env_id(未指定なら workspace 名)は、英小文字・数字・ハイフンの20文字以内にしてください。"
    }
  }
}

data "http" "my_ip" {
  count = var.access_mode == "direct" && length(var.allowed_cidrs) == 0 ? 1 : 0

  url = "https://checkip.amazonaws.com"

  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && can(cidrnetmask("${chomp(self.response_body)}/32"))
      error_message = "グローバルIPを取得できませんでした。allowed_cidrs を指定してください。"
    }
  }
}

module "sizing" {
  source = "./modules/sizing"

  models_file         = "${path.module}/../catalog/models.yaml"
  instance_types_file = "${path.module}/../catalog/instance_types.yaml"
  model               = var.model
  context_length      = var.context_length
  kv_cache_type       = var.kv_cache_type
  vram_overhead_gib   = var.vram_overhead_gib
  instance_type       = var.instance_type
}

module "network" {
  source = "./modules/network"

  name     = local.name
  region   = var.region
  vpc_cidr = var.vpc_cidr
  az_count = var.az_count
  tags     = local.resource_tags
}

module "security" {
  source = "./modules/security"

  name          = local.name
  vpc_id        = module.network.vpc_id
  ollama_port   = local.ollama_port
  allowed_cidrs = local.allowed_cidrs
  tags          = local.resource_tags
}

data "aws_caller_identity" "current" {}

# S3キャッシュ: terraform/model-store で作ったバケットがあることを plan で確かめる
data "aws_s3_bucket" "models" {
  count = var.model_storage == "s3_cache" ? 1 : 0

  bucket = coalesce(var.model_bucket_name, "llm-launchpad-models-${data.aws_caller_identity.current.account_id}-${var.region}")
}

# EBS永続: terraform/model-store で作ったボリュームを名前(ModelVolume タグ)で探す
data "aws_ebs_volumes" "models" {
  count = var.model_storage == "ebs" ? 1 : 0

  tags = {
    Project     = local.project
    ModelVolume = var.model_volume_name
  }
}

data "aws_ebs_volume" "models" {
  count = var.model_storage == "ebs" ? 1 : 0

  filter {
    name   = "volume-id"
    values = data.aws_ebs_volumes.models[0].ids
  }

  lifecycle {
    precondition {
      condition     = length(data.aws_ebs_volumes.models[0].ids) == 1
      error_message = "ModelVolume=${var.model_volume_name} の EBS ボリュームが ${var.region} に${length(data.aws_ebs_volumes.models[0].ids) == 0 ? "ありません。terraform/model-store の model_volumes で作ってください" : "複数あります"}。"
    }

    # EBS は作ったAZのインスタンスにしか付けられない
    postcondition {
      condition     = var.availability_zone == null || self.availability_zone == var.availability_zone
      error_message = "EBS ボリュームは ${self.availability_zone} にあるため、availability_zone は指定しないか ${self.availability_zone} にしてください。"
    }
  }
}

module "bootstrap" {
  source = "./modules/bootstrap"

  name                  = local.name
  region                = var.region
  status_parameter_name = "/${local.project}/${local.env_id}/status"
  model                 = var.model
  context_length        = var.context_length
  kv_cache_type         = var.kv_cache_type
  ollama_version        = var.ollama_version
  ollama_listen_all     = var.access_mode == "direct"
  ollama_port           = local.ollama_port
  model_storage         = var.model_storage
  model_bucket_name     = local.model_bucket_name
  model_volume_id       = local.model_volume_id
  idle_stop_minutes     = var.idle_stop_minutes
  tags                  = local.resource_tags
}

module "iam" {
  source = "./modules/iam"

  name_prefix          = local.iam_name_prefix
  model_bucket_name    = local.model_bucket_name
  status_parameter_arn = module.bootstrap.status_parameter_arn
  tags                 = local.resource_tags
}

module "instance" {
  source = "./modules/instance"

  name                   = local.name
  instance_type          = module.sizing.instance_type
  purchase_option        = var.purchase_option
  subnet_ids             = module.network.subnet_ids
  availability_zone      = local.availability_zone
  security_group_id      = module.security.security_group_id
  instance_profile_name  = module.iam.instance_profile_name
  model_file_size_gb     = module.sizing.model.file_size_gb
  models_on_root_volume  = var.model_storage != "ebs"
  model_volume_id        = local.model_volume_id
  root_volume_size_gib   = var.root_volume_size_gib
  root_volume_throughput = var.root_volume_throughput
  root_volume_iops       = var.root_volume_iops
  user_data_base64       = module.bootstrap.user_data_base64
  tags                   = local.resource_tags
}

module "lifetime" {
  source = "./modules/lifetime"
  count  = var.max_lifetime_hours > 0 ? 1 : 0

  name_prefix        = "${local.iam_name_prefix}ttl-"
  instance_id        = module.instance.instance_id
  instance_arn       = module.instance.instance_arn
  max_lifetime_hours = var.max_lifetime_hours
  tags               = local.resource_tags
}
