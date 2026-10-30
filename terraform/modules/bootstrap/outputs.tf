output "user_data" {
  description = "GPUインスタンスの user data"
  value       = local.user_data
}

output "user_data_base64" {
  description = "gzip で圧縮した user data(base64)。user data の上限 16KB に収めるため、cloud-init が展開して実行する"
  value       = base64gzip(local.user_data)
}

output "models_dir" {
  description = "インスタンス上で Ollama がモデルを置くディレクトリ(OLLAMA_MODELS)"
  value       = local.models_dir
}

output "status_parameter_name" {
  description = "起動処理の進み具合を書く SSM パラメータの名前"
  value       = aws_ssm_parameter.status.name
}

output "status_parameter_arn" {
  description = "起動処理の進み具合を書く SSM パラメータの ARN"
  value       = aws_ssm_parameter.status.arn
}

output "ollama_install_key" {
  description = "閉じたネットワークで使う Ollama のリリースファイルの S3 キー"
  value       = local.ollama_install_key
}
