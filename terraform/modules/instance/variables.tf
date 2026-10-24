variable "name" {
  description = "Name タグに使う名前"
  type        = string
}

variable "instance_type" {
  description = "GPUインスタンス型"
  type        = string
}

variable "purchase_option" {
  description = "購入方法(spot / on-demand)"
  type        = string
}

variable "subnet_ids" {
  description = "AZ名ごとのサブネットID"
  type        = map(string)
}

variable "availability_zone" {
  description = "起動するAZ。null ならインスタンス型を提供しているAZから選ぶ"
  type        = string
  default     = null
}

variable "security_group_id" {
  description = "インスタンスに付けるセキュリティグループのID"
  type        = string
}

variable "instance_profile_name" {
  description = "インスタンスプロファイル名"
  type        = string
}

variable "model_file_size_gb" {
  description = "モデルの配布サイズ(GB)。ルートボリュームの既定の大きさに使う"
  type        = number
}

variable "root_volume_size_gib" {
  description = "ルートボリュームの大きさ(GiB)。null なら AMI とモデルの大きさから決める"
  type        = number
  default     = null
}

variable "user_data" {
  description = "初回起動時に実行する user data。変わったらインスタンスを作り直す"
  type        = string
}

variable "tags" {
  description = "追加で付けるタグ"
  type        = map(string)
  default     = {}
}
