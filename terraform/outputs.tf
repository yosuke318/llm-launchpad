output "env_id" {
  description = "環境ID"
  value       = local.env_id
}

output "region" {
  description = "リージョン"
  value       = var.region
}

output "model" {
  description = "動かすモデルの定義"
  value       = module.sizing.model
}

output "instance_type" {
  description = "選んだGPUインスタンス型"
  value       = module.sizing.instance_type
}

output "instance" {
  description = "選んだGPUインスタンス型の仕様"
  value       = module.sizing.instance
}

output "vram_estimate" {
  description = "必要VRAMの見積もりの内訳(GiB)。重み + KVキャッシュ + 余裕"
  value       = module.sizing.vram_estimate
}

output "fitting_instance_types" {
  description = "見積もりが収まるインスタンス型(選ばれる順)"
  value       = module.sizing.fitting_instance_types
}

output "vpc_id" {
  description = "環境用VPCのID"
  value       = module.network.vpc_id
}

output "subnet_ids" {
  description = "AZ名ごとのパブリックサブネットID"
  value       = module.network.subnet_ids
}

output "security_group_id" {
  description = "GPUインスタンスに付けるセキュリティグループのID"
  value       = module.security.security_group_id
}

output "instance_profile_name" {
  description = "GPUインスタンスに付けるインスタンスプロファイル名"
  value       = module.iam.instance_profile_name
}

output "instance_role_arn" {
  description = "GPUインスタンスのIAMロールARN"
  value       = module.iam.role_arn
}

output "access_mode" {
  description = "Ollama API への接続方法(ssm / direct)"
  value       = var.access_mode
}

output "allowed_cidrs" {
  description = "Ollama API への接続を許可しているCIDR(ssm モードでは空)"
  value       = module.security.allowed_cidrs
}

output "tags" {
  description = "全リソースに付けている管理用タグ"
  value       = merge(local.default_tags, local.resource_tags)
}
