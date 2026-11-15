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

  private = var.network_mode == "private"

  # vLLM(engine = vllm のとき)。Ollama と同じポートで OpenAI 互換 API を出すので、接続のしかたは変わらない
  vllm = var.engine == "vllm"
  # T4(g4dn)は bf16 を扱えない(compute capability 7.5)ので fp16 で動かす
  vllm_dtype = strcontains(module.sizing.instance.gpu_model, "T4") ? "half" : "auto"
  # vLLM のイメージ(CUDA を含み大きい)の分、ルートボリュームを足す
  vllm_root_volume_gib = local.vllm ? 30 : 0

  # Open WebUI(open_webui = true のとき)。インスタンスの 127.0.0.1 だけで待ち受ける
  open_webui_port  = 8080
  open_webui_image = "ghcr.io/open-webui/open-webui:${var.open_webui_version}"
  # Open WebUI のイメージと、それが持つデータ(会話や埋め込みモデル)の分、ルートボリュームを足す
  open_webui_root_volume_gib = var.open_webui ? 10 : 0

  # モデルの置き場所(model_storage)ごとのバケットとボリューム。使わない方式では null
  model_bucket_name = contains(["s3_cache", "s3_files"], var.model_storage) ? data.aws_s3_bucket.models[0].bucket : null
  s3_files          = var.model_storage == "s3_files"

  # 閉じたネットワークでは、Ollama のリリースファイルもモデル用のバケットに置く
  ollama_install_bucket_name = local.private ? data.aws_s3_bucket.models[0].bucket : null
  model_volume_id            = var.model_storage == "ebs" ? data.aws_ebs_volume.models[0].volume_id : null

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
