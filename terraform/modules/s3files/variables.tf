variable "name" {
  description = "Name タグに使う名前"
  type        = string
}

variable "name_prefix" {
  description = "IAM ロールの名前の先頭"
  type        = string
}

variable "bucket_name" {
  description = "model-store のバケット名。S3 Files のためにバージョニングを有効にしておく(terraform/model-store の enable_versioning)"
  type        = string
}

variable "prefix" {
  description = "ファイルシステムにするバケットの中の場所。Ollama の models ディレクトリと同じ構成(manifests/ と blobs/)"
  type        = string
  default     = "ollama/models/"
}

variable "vpc_id" {
  description = "マウントターゲットを置く VPC"
  type        = string
}

variable "subnet_ids" {
  description = "マウントターゲットを置くサブネット。AZ 名ごとのサブネットID(AZ ごとに1つ)"
  type        = map(string)
}

variable "instance_security_group_id" {
  description = "GPU インスタンスのセキュリティグループ。マウントターゲットへの NFS を通す"
  type        = string
}

variable "instance_role_name" {
  description = "GPU インスタンスの IAM ロール。マウントの権限を足す"
  type        = string
}

variable "tags" {
  description = "追加で付けるタグ"
  type        = map(string)
  default     = {}
}
