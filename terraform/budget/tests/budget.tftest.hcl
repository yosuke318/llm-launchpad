# AWSに接続せずに plan の結果を確かめるテスト。terraform/budget で terraform test を実行する

mock_provider "aws" {}

variables {
  notification_emails = ["you@example.com"]
}

run "monthly_budget_with_notifications" {
  command = plan

  assert {
    condition     = aws_budgets_budget.monthly.limit_amount == "50.00" && aws_budgets_budget.monthly.time_unit == "MONTHLY"
    error_message = "既定は月 50 USD の予算であること"
  }

  # 実際の料金の 50 / 80 / 100% と、予測の 100%
  assert {
    condition     = length(aws_budgets_budget.monthly.notification) == 4
    error_message = "実際の料金のしきい値ごとと、予測の通知があること"
  }

  assert {
    condition     = length(aws_budgets_budget.monthly.cost_filter) == 0
    error_message = "既定ではアカウント全体の料金を数えること"
  }
}

run "filter_by_project_tag" {
  command = plan

  variables {
    filter_by_project_tag = true
  }

  assert {
    condition     = one(aws_budgets_budget.monthly.cost_filter).values == tolist(["user:Project$llm-launchpad"])
    error_message = "Project タグで絞ること"
  }
}

run "rejects_missing_email" {
  command = plan

  variables {
    notification_emails = []
  }

  expect_failures = [var.notification_emails]
}
