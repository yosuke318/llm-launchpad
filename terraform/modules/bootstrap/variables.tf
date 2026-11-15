variable "name" {
  description = "Name タグに使う名前"
  type        = string
}

variable "region" {
  description = "リージョン"
  type        = string
}

variable "status_parameter_name" {
  description = "起動処理の進み具合を書く SSM パラメータの名前"
  type        = string
}

variable "model" {
  description = "Ollama のタグ"
  type        = string
}

variable "context_length" {
  description = "Ollama に設定するコンテキスト長(OLLAMA_CONTEXT_LENGTH)"
  type        = number
}

variable "kv_cache_type" {
  description = "Ollama に設定するKVキャッシュの型(OLLAMA_KV_CACHE_TYPE)"
  type        = string
}

variable "ollama_version" {
  description = "入れる Ollama のバージョン(例: 0.12.6)。null なら最新"
  type        = string
  default     = null
}

variable "ollama_listen_all" {
  description = "true なら Ollama を全てのアドレスで待ち受ける(direct モード)。false なら 127.0.0.1 だけ"
  type        = bool
}

variable "ollama_port" {
  description = "Ollama API のポート"
  type        = number
}

variable "model_storage" {
  description = "モデルの置き場所(s3_cache / ebs / download)"
  type        = string
}

variable "model_bucket_name" {
  description = "s3_cache でモデルを置く S3 バケット名"
  type        = string
  default     = null
}

variable "model_volume_id" {
  description = "ebs でモデルを置く EBS ボリュームのID"
  type        = string
  default     = null
}

variable "idle_stop_minutes" {
  description = "Ollama へのリクエストがこの分数なければインスタンスを止める。0 なら止めない"
  type        = number
  default     = 0
}

variable "offline" {
  description = "true なら閉じたネットワーク用にする。Ollama をバケットのリリースファイルから入れ、Ollama library には問い合わせない"
  type        = bool
  default     = false
}

variable "ollama_install_bucket_name" {
  description = "offline で、Ollama のリリースファイル(ollama/install/<バージョン>/ollama-linux-amd64.tar.zst)を置いたS3バケット名"
  type        = string
  default     = null
}

variable "engine" {
  description = "推論エンジン(ollama / vllm)"
  type        = string
  default     = "ollama"
}

variable "vllm_hf_repo" {
  description = "engine = vllm で動かすモデルの Hugging Face のリポジトリ"
  type        = string
  default     = null
}

variable "vllm_image" {
  description = "vLLM のイメージ"
  type        = string
  default     = "vllm/vllm-openai:latest"
}

variable "vllm_tensor_parallel_size" {
  description = "vLLM でモデルを分けて載せる GPU の数(インスタンスの GPU の数)"
  type        = number
  default     = 1
}

variable "vllm_dtype" {
  description = "vLLM の --dtype。auto か half(bf16 を扱えない T4 のとき)"
  type        = string
  default     = "auto"
}

variable "vllm_weights_gb" {
  description = "取得する重みの大きさ(GB)。取得の進み具合を出すのに使う"
  type        = number
  default     = 0
}

variable "s3files_file_system_id" {
  description = "model_storage = s3_files でマウントする S3 Files のファイルシステムの ID"
  type        = string
  default     = null
}

variable "hf_token_parameter_name" {
  description = "Hugging Face のトークンを入れた SSM パラメータ(SecureString)の名前。null ならトークンなしで取得する"
  type        = string
  default     = null
}

variable "open_webui" {
  description = "true なら Open WebUI も入れる(Docker で動かし、127.0.0.1 だけで待ち受ける)"
  type        = bool
  default     = false
}

variable "open_webui_image" {
  description = "Open WebUI のイメージ"
  type        = string
  default     = "ghcr.io/open-webui/open-webui:main"
}

variable "open_webui_port" {
  description = "Open WebUI が待ち受けるポート(127.0.0.1)"
  type        = number
  default     = 8080
}

variable "tags" {
  description = "追加で付けるタグ"
  type        = map(string)
  default     = {}
}
