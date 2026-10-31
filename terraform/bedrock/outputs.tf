output "env_id" {
  description = "環境ID"
  value       = local.env_id
}

output "region" {
  description = "リージョン"
  value       = var.region
}

output "model" {
  description = "呼ぶモデルの定義(catalog/bedrock_models.yaml)と、推論プロファイルが振り分ける先のモデル"
  value = merge(local.model, {
    inference_profile_arn = data.aws_bedrock_inference_profile.this.inference_profile_arn
    routed_model_arns     = data.aws_bedrock_inference_profile.this.models[*].model_arn
  })
}

output "availability_zone" {
  description = "Lambda と bedrock-runtime のエンドポイントを置いたAZ"
  value       = local.availability_zone
}

output "function_name" {
  description = "Bedrock を呼ぶ Lambda の関数名"
  value       = aws_lambda_function.this.function_name
}

output "vpc_id" {
  description = "環境用VPCのID"
  value       = module.network.vpc_id
}

output "vpc_endpoint_ids" {
  description = "bedrock-runtime のインターフェイスエンドポイントのID"
  value       = module.network.vpc_endpoint_ids
}

output "invoke_command" {
  description = "Lambda にプロンプトを送るコマンド。結果は response.json に書かれる"
  value = join(" ", compact([
    "aws lambda invoke --function-name ${aws_lambda_function.this.function_name} --region ${var.region}",
    var.aws_profile == null ? "" : "--profile ${var.aws_profile}",
    "--cli-binary-format raw-in-base64-out",
    "--payload '{\"action\":\"chat\",\"messages\":[{\"role\":\"user\",\"content\":\"こんにちは\"}]}' response.json",
  ]))
}

output "tags" {
  description = "全リソースに付けている管理用タグ"
  value       = merge(local.default_tags, local.resource_tags)
}
