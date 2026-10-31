variable "name" {
  description = "Name タグに使う名前"
  type        = string
}

variable "region" {
  description = "リージョン(S3 エンドポイントのサービス名に使う)"
  type        = string
}

variable "vpc_cidr" {
  description = "VPCのCIDR"
  type        = string
}

variable "az_count" {
  description = "サブネットを作るAZの数"
  type        = number
}

variable "private" {
  description = "true なら閉じたネットワークにする。インターネットゲートウェイを作らず、SSM 接続用のエンドポイントを作る"
  type        = bool
  default     = false
}

variable "endpoint_availability_zone" {
  description = "private = true で、SSM 接続用のエンドポイントを置くAZ(GPUインスタンスを起動するAZ)"
  type        = string
  default     = null
}

variable "tags" {
  description = "各リソースに追加で付けるタグ"
  type        = map(string)
  default     = {}
}
