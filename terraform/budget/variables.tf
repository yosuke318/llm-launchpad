variable "aws_profile" {
  description = "使うAWS CLIのプロファイル名。null なら環境変数や既定の認証情報を使う"
  type        = string
  default     = null
}

variable "budget_name" {
  description = "予算の名前"
  type        = string
  default     = "llm-launchpad-monthly"
}

variable "monthly_limit_usd" {
  description = "月の予算(USD)"
  type        = number
  default     = 50

  validation {
    condition     = var.monthly_limit_usd > 0
    error_message = "monthly_limit_usd は 0 より大きい値にしてください。"
  }
}

variable "notification_emails" {
  description = "通知を送るメールアドレス"
  type        = list(string)

  validation {
    condition     = length(var.notification_emails) > 0 && alltrue([for e in var.notification_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", e))])
    error_message = "notification_emails にはメールアドレスを1つ以上指定してください。"
  }
}

variable "actual_thresholds_percent" {
  description = "実際の料金が予算のこの割合(%)を超えたら知らせる"
  type        = list(number)
  default     = [50, 80, 100]
}

variable "filter_by_project_tag" {
  description = "true なら Project=llm-launchpad のタグが付いた料金だけを数える。false ならアカウント全体の料金を数える"
  type        = bool
  default     = false
}
