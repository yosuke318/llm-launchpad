output "env_id" {
  description = "環境ID"
  value       = local.env_id
}

output "region" {
  description = "リージョン"
  value       = var.region
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
