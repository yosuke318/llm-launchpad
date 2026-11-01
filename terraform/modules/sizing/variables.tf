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

variable "model_definition" {
  description = "モデル定義(models_file)にないモデルの定義。項目は models_file と同じ。null ならモデル定義から探す"
  type = object({
    vendor           = optional(string, "")
    name             = optional(string, "")
    params_b         = optional(number, 0)
    quantization     = optional(string, "")
    file_size_gb     = number
    max_context      = number
    layers           = number
    attention_layers = optional(number)
    kv_heads         = number
    head_dim         = number
    license          = optional(string, "")
  })
  default = null
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
