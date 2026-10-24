output "user_data" {
  description = "GPUインスタンスの user data"
  value       = local.user_data
}

output "status_parameter_name" {
  description = "起動処理の進み具合を書く SSM パラメータの名前"
  value       = aws_ssm_parameter.status.name
}

output "status_parameter_arn" {
  description = "起動処理の進み具合を書く SSM パラメータの ARN"
  value       = aws_ssm_parameter.status.arn
}
