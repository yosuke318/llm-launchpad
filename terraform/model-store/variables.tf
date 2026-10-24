variable "region" {
  description = "モデルを置くリージョン。GPUインスタンスと同じリージョンにする(同じリージョンの S3 から EC2 への転送は無料)"
  type        = string
  default     = "ap-northeast-1"
}

variable "aws_profile" {
  description = "使うAWS CLIのプロファイル名。null なら環境変数や既定の認証情報を使う"
  type        = string
  default     = null
}

variable "create_bucket" {
  description = "S3キャッシュ用のバケットを作るか"
  type        = bool
  default     = true
}

variable "bucket_name" {
  description = "S3キャッシュ用のバケット名。null なら llm-launchpad-models-<アカウントID>-<リージョン>"
  type        = string
  default     = null
}

variable "model_volumes" {
  description = "EBS永続用のボリューム。キーがボリューム名(環境側の model_volume_name で指定する)"
  type = map(object({
    availability_zone = string
    size_gib          = number
    # gp3 の既定(3000 IOPS、125 MiB/s)より上げると、停止中も月額がかかる
    iops       = optional(number, 3000)
    throughput = optional(number, 250)
  }))
  default = {}

  validation {
    condition     = alltrue([for k, v in var.model_volumes : can(regex("^[a-z0-9][a-z0-9-]{0,30}$", k))])
    error_message = "model_volumes のキーは英小文字・数字・ハイフンの31文字以内にしてください。"
  }

  validation {
    condition     = alltrue([for v in values(var.model_volumes) : v.size_gib >= 10 && v.size_gib <= 16384])
    error_message = "size_gib は 10〜16384 の範囲で指定してください。"
  }

  validation {
    condition     = alltrue([for v in values(var.model_volumes) : v.throughput >= 125 && v.throughput <= 1000])
    error_message = "throughput は 125〜1000 MiB/s の範囲で指定してください。"
  }
}
