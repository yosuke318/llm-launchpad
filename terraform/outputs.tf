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

output "instance_id" {
  description = "GPUインスタンスのID"
  value       = module.instance.instance_id
}

output "purchase_option" {
  description = "購入方法(spot / on-demand)"
  value       = var.purchase_option
}

output "availability_zone" {
  description = "GPUインスタンスを起動したAZ"
  value       = module.instance.availability_zone
}

output "public_ip" {
  description = "GPUインスタンスのパブリックIP"
  value       = module.instance.public_ip
}

output "ami" {
  description = "使った Deep Learning Base AMI"
  value = {
    id   = module.instance.ami_id
    name = module.instance.ami_name
  }
}

output "root_volume_size_gib" {
  description = "ルートボリュームの大きさ(GiB)"
  value       = module.instance.root_volume_size_gib
}

output "ollama_endpoint" {
  description = "Ollama API のエンドポイント。ssm モードでは、ssm_port_forward_command を実行している間だけ使える"
  value       = local.ollama_endpoint
}

output "ssm_session_command" {
  description = "インスタンスにシェルで入るコマンド"
  value       = "aws ssm start-session --target ${module.instance.instance_id} ${local.aws_cli_options}"
}

output "ssm_port_forward_command" {
  description = "Ollama API(11434)を手元の localhost:11434 に転送するコマンド(ssm モード)"
  value = var.access_mode != "ssm" ? null : join(" ", [
    "aws ssm start-session --target ${module.instance.instance_id} ${local.aws_cli_options}",
    "--document-name AWS-StartPortForwardingSession",
    "--parameters '{\"portNumber\":[\"${local.ollama_port}\"],\"localPortNumber\":[\"${local.ollama_port}\"]}'",
  ])
}

output "curl_example" {
  description = "Ollama API にプロンプトを送る例"
  value       = "curl ${local.ollama_endpoint}/api/generate -d '{\"model\":\"${var.model}\",\"prompt\":\"こんにちは\",\"stream\":false}'"
}

output "openai_api_example" {
  description = "OpenAI 互換API(/v1)に送る例。OpenAI の SDK では base_url に /v1 までを指定し、api_key は任意の文字列でよい"
  value       = "curl ${local.ollama_endpoint}/v1/chat/completions -H 'Content-Type: application/json' -d '{\"model\":\"${var.model}\",\"messages\":[{\"role\":\"user\",\"content\":\"こんにちは\"}]}'"
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
