variable "name_prefix" {
  description = "IAMロールとインスタンスプロファイルの名前の接頭辞(38文字以内)"
  type        = string

  validation {
    condition     = length(var.name_prefix) <= 38
    error_message = "name_prefix は38文字以内にしてください。"
  }
}

variable "model_bucket_name" {
  description = "モデルを置くS3バケット名。null なら権限を付けない"
  type        = string
  default     = null
}

variable "status_parameter_arn" {
  description = "起動処理の進み具合を書く SSM パラメータの ARN。インスタンスに書き込み権限を付ける"
  type        = string
}

variable "tags" {
  description = "各リソースに追加で付けるタグ"
  type        = map(string)
  default     = {}
}
