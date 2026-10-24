output "budget_name" {
  description = "予算の名前"
  value       = aws_budgets_budget.monthly.name
}

output "monthly_limit_usd" {
  description = "月の予算(USD)"
  value       = aws_budgets_budget.monthly.limit_amount
}
