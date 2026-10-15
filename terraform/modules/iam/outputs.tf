output "role_arn" {
  description = "IAMロールのARN"
  value       = aws_iam_role.instance.arn
}

output "role_name" {
  description = "IAMロール名(後続のIssueでポリシーを足すときに使う)"
  value       = aws_iam_role.instance.name
}

output "instance_profile_name" {
  description = "インスタンスプロファイル名"
  value       = aws_iam_instance_profile.instance.name
}

output "model_bucket_read_enabled" {
  description = "モデルバケットの読み取り権限を付けているか"
  value       = length(aws_iam_role_policy.model_bucket_read) > 0
}
