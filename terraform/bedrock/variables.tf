variable "region" {
  description = "Bedrock を呼ぶリージョン"
  type        = string
  default     = "ap-northeast-1"
}

variable "aws_profile" {
  description = "使うAWS CLIのプロファイル名。null なら環境変数や既定の認証情報を使う"
  type        = string
  default     = null
}

variable "env_id" {
  description = "環境ID。null なら workspace 名を使う"
  type        = string
  default     = null
}

variable "model" {
  description = "呼ぶモデルの推論プロファイルID(catalog/bedrock_models.yaml の id)"
  type        = string
  default     = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "vpc_cidr" {
  description = "環境用VPCのCIDR。/16〜/24 の範囲で指定する(サブネットは /+8 で切る)"
  type        = string
  default     = "10.43.0.0/16"

  validation {
    condition = (
      can(cidrnetmask(var.vpc_cidr)) &&
      can(tonumber(split("/", var.vpc_cidr)[1])) &&
      try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 24, false)
    )
    error_message = "vpc_cidr は /16〜/24 のIPv4 CIDRで指定してください。"
  }
}

variable "availability_zone" {
  description = "サブネットと bedrock-runtime のエンドポイントを置くAZ。null なら名前順で最初のAZ"
  type        = string
  default     = null
}

variable "log_retention_days" {
  description = "Lambda のログを残す日数"
  type        = number
  default     = 7
}

variable "extra_tags" {
  description = "全リソースに追加で付けるタグ"
  type        = map(string)
  default     = {}
}
