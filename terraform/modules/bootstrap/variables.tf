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

variable "tags" {
  description = "追加で付けるタグ"
  type        = map(string)
  default     = {}
}
