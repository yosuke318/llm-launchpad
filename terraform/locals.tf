locals {
  project = "llm-launchpad"

  # 1 workspace = 1 環境。env_id を明示しなければ workspace 名を使う
  env_id = coalesce(var.env_id, terraform.workspace)

  # Name タグ用
  name = "${local.project}-${local.env_id}"

  # IAM の name_prefix は38文字までのため短い接頭辞を使う
  iam_name_prefix = "llm-lp-${local.env_id}-"

  ollama_port = 11434

  # 管理用タグは extra_tags で上書きできないよう後ろに置く
  default_tags = merge(var.extra_tags, {
    Project   = local.project
    EnvId     = local.env_id
    ManagedBy = "terraform"
  })

  # CreatedAt は plan のたびに変わらないよう time_static から取る。
  # provider の default_tags はリソースに依存できないため、各リソースの tags で付ける
  resource_tags = {
    CreatedAt = time_static.created.rfc3339
  }

  # モデルの置き場所(model_storage)ごとのバケットとボリューム。使わない方式では null
  model_bucket_name = var.model_storage == "s3_cache" ? data.aws_s3_bucket.models[0].bucket : null
  model_volume_id   = var.model_storage == "ebs" ? data.aws_ebs_volume.models[0].volume_id : null

  # EBS永続では、ボリュームのあるAZに起動する
  availability_zone = var.model_storage == "ebs" ? data.aws_ebs_volume.models[0].availability_zone : var.availability_zone

  # 接続のしかたに出す AWS CLI の共通オプション
  aws_cli_options = join(" ", compact(["--region ${var.region}", var.aws_profile == null ? "" : "--profile ${var.aws_profile}"]))

  # ssm モードは SSM のポートフォワードで手元の localhost に転送する
  ollama_endpoint = var.access_mode == "ssm" ? "http://localhost:${local.ollama_port}" : "http://${module.instance.public_ip}:${local.ollama_port}"

  # direct モードで allowed_cidrs が空なら、実行したマシンのグローバルIPを使う
  allowed_cidrs = var.access_mode != "direct" ? [] : (
    length(var.allowed_cidrs) > 0 ? var.allowed_cidrs : ["${chomp(data.http.my_ip[0].response_body)}/32"]
  )
}
