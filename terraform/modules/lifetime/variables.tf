variable "name_prefix" {
  description = "IAMロールとスケジュールの名前の接頭辞(38文字以内)"
  type        = string
}

variable "instance_id" {
  description = "止めるインスタンスのID"
  type        = string
}

variable "instance_arn" {
  description = "止めるインスタンスのARN"
  type        = string
}

variable "max_lifetime_hours" {
  description = "インスタンスを起動してから止めるまでの時間"
  type        = number
}

variable "tags" {
  description = "追加で付けるタグ"
  type        = map(string)
  default     = {}
}
