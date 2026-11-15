terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# 起動処理の進み具合。インスタンスが書き換えるため、Terraform は作るときの値だけを持つ
resource "aws_ssm_parameter" "status" {
  name        = var.status_parameter_name
  description = "Bootstrap status of ${var.name}"
  type        = "String"
  tier        = "Standard"
  value       = jsonencode({ stage = "pending", state = "running" })

  tags = merge(var.tags, { Name = var.name })

  lifecycle {
    ignore_changes = [value]

    precondition {
      condition     = var.model_storage != "s3_cache" || var.model_bucket_name != null
      error_message = "model_storage = s3_cache には model_bucket_name が要ります。"
    }

    precondition {
      condition     = !var.offline || var.ollama_install_bucket_name != null
      error_message = "offline(閉じたネットワーク)には ollama_install_bucket_name が要ります。"
    }

    precondition {
      condition     = !var.offline || var.model_storage != "download"
      error_message = "offline(閉じたネットワーク)では model_storage = download は使えません。"
    }

    precondition {
      condition     = var.engine != "vllm" || (var.vllm_hf_repo != null && var.model_storage == "download" && !var.offline)
      error_message = "engine = vllm には vllm_hf_repo が要り、model_storage = download でパブリックなネットワークだけを使えます。"
    }

    precondition {
      condition     = !(var.offline && var.open_webui)
      error_message = "offline(閉じたネットワーク)では Open WebUI は入れられません(イメージを ghcr.io から取得するため)。"
    }

    precondition {
      condition     = var.model_storage != "s3_files" || (var.s3files_file_system_id != null && !var.offline && var.engine == "ollama")
      error_message = "model_storage = s3_files には s3files_file_system_id が要り、パブリックなネットワークと Ollama だけで使えます。"
    }

    precondition {
      condition     = var.model_storage != "ebs" || var.model_volume_id != null
      error_message = "model_storage = ebs には model_volume_id が要ります。"
    }
  }
}

locals {
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    region             = var.region
    status_parameter   = aws_ssm_parameter.status.name
    launchpad_py       = trimsuffix(file("${path.module}/files/launchpad.py"), "\n")
    model              = var.model
    context_length     = var.context_length
    kv_cache_type      = var.kv_cache_type
    ollama_version     = var.ollama_version == null ? "" : var.ollama_version
    ollama_host        = var.ollama_listen_all ? "0.0.0.0" : "127.0.0.1"
    ollama_port        = var.ollama_port
    model_storage      = var.model_storage
    models_dir         = local.models_dir
    bucket             = var.model_bucket_name == null ? "" : var.model_bucket_name
    volume_id          = var.model_volume_id == null ? "" : var.model_volume_id
    mount_point        = local.ebs_mount_point
    idle_stop_minutes  = var.idle_stop_minutes
    offline            = var.offline
    install_bucket     = var.ollama_install_bucket_name == null ? "" : var.ollama_install_bucket_name
    ollama_install_key = local.ollama_install_key
    engine             = var.engine
    vllm_hf_repo       = var.vllm_hf_repo == null ? "" : var.vllm_hf_repo
    vllm_image         = var.vllm_image
    vllm_tp            = var.vllm_tensor_parallel_size
    vllm_dtype         = var.vllm_dtype
    vllm_weights_bytes = floor(var.vllm_weights_gb * 1e9)
    hf_cache_dir       = "/opt/llm-launchpad/hf-cache"
    hf_token_parameter = var.hf_token_parameter_name == null ? "" : var.hf_token_parameter_name
    s3files_fs_id      = var.s3files_file_system_id == null ? "" : var.s3files_file_system_id
    open_webui         = var.open_webui
    open_webui_image   = var.open_webui_image
    open_webui_port    = var.open_webui_port
  })

  ebs_mount_point = "/mnt/llm-models"

  # 閉じたネットワークで使う Ollama のリリースファイル。バージョンを指定しなければ latest の下に置いたものを使う
  ollama_install_key = "ollama/install/${coalesce(var.ollama_version, "latest")}/ollama-linux-amd64.tar.zst"

  # EBS永続ではボリュームの中に置く。S3 Files ではマウントしたところ(バケットの ollama/models/)をそのまま使う。
  # それ以外はルートボリュームの Ollama の既定の場所に置く
  models_dir = var.model_storage == "ebs" ? "${local.ebs_mount_point}/ollama" : (var.model_storage == "s3_files" ? local.ebs_mount_point : "/usr/share/ollama/.ollama/models")
}
