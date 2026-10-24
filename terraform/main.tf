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
  tags                  = local.resource_tags
}

module "iam" {
  source = "./modules/iam"

  name_prefix          = local.iam_name_prefix
  model_bucket_name    = var.model_bucket_name
  status_parameter_arn = module.bootstrap.status_parameter_arn
  tags                 = local.resource_tags
}

module "instance" {
  source = "./modules/instance"

  name                  = local.name
  instance_type         = module.sizing.instance_type
  purchase_option       = var.purchase_option
  subnet_ids            = module.network.subnet_ids
  availability_zone     = var.availability_zone
  security_group_id     = module.security.security_group_id
  instance_profile_name = module.iam.instance_profile_name
  model_file_size_gb    = module.sizing.model.file_size_gb
  root_volume_size_gib  = var.root_volume_size_gib
  user_data             = module.bootstrap.user_data
  tags                  = local.resource_tags
}
