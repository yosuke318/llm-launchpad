variable "models_file" {
  description = "モデル定義(YAML)のパス"
  type        = string
}

variable "instance_types_file" {
  description = "インスタンス型の表(YAML)のパス"
  type        = string
}

variable "model" {
  description = "Ollama のタグ(モデル定義のキー)"
  type        = string
}

variable "context_length" {
  description = "コンテキスト長(トークン)"
  type        = number
}

variable "kv_cache_type" {
  description = "KVキャッシュの型(f16 / q8_0 / q4_0)"
  type        = string
}

variable "vram_overhead_gib" {
  description = "重みとKVキャッシュのほかに見込む余裕(GiB)。CUDAコンテキストや計算用バッファの分"
  type        = number
}

variable "instance_type" {
  description = "インスタンス型を手で指定する場合の型名。null なら自動で選ぶ"
  type        = string
  default     = null
}
