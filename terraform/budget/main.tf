# 月の予算を超えそうなときにメールで知らせる(AWS Budgets)。予算はアカウント単位なので、
# GPUインスタンスの環境とは別の state で管理し、環境を destroy しても残るようにする

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.68"
    }
  }
}

provider "aws" {
  # AWS Budgets はリージョンによらない。料金はアカウント全体(全リージョン)で数える
  region  = "us-east-1"
  profile = var.aws_profile

  default_tags {
    tags = {
      Project    = "llm-launchpad"
      EnvId      = "budget"
      ManagedBy  = "terraform"
      Persistent = "true"
    }
  }
}

resource "aws_budgets_budget" "monthly" {
  name         = var.budget_name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Project タグで絞る。コスト配分タグとして Project を有効にしておく必要がある(有効にする前の料金は数えられない)
  dynamic "cost_filter" {
    for_each = var.filter_by_project_tag ? [1] : []

    content {
      name   = "TagKeyValue"
      values = ["user:Project$llm-launchpad"]
    }
  }

  # 実際の料金がしきい値を超えたとき
  dynamic "notification" {
    for_each = toset(var.actual_thresholds_percent)

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = var.notification_emails
    }
  }

  # 月末までの予測が予算を超えそうなとき
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.notification_emails
  }
}
