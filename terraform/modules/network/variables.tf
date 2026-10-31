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
  description = "true なら閉じたネットワークにする。インターネットゲートウェイを作らず、interface_endpoint_services のエンドポイントを作る"
  type        = bool
  default     = false
}

variable "endpoint_availability_zone" {
  description = "private = true で、インターフェイスエンドポイントを置くAZ(GPUインスタンスを起動するAZ)"
  type        = string
  default     = null
}

variable "interface_endpoint_services" {
  description = "private = true で作るインターフェイスエンドポイントのサービス名(com.amazonaws.<リージョン>. の後ろ)"
  type        = list(string)
  # ssm: 状態の書き込みと SSM エージェントの登録 / ec2messages と ssmmessages: Session Manager の接続
  default = ["ssm", "ec2messages", "ssmmessages"]
}

variable "tags" {
  description = "各リソースに追加で付けるタグ"
  type        = map(string)
  default     = {}
}
