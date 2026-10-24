output "stop_at" {
  description = "インスタンスを止める時刻(UTC、RFC 3339)"
  value       = local.stop_at
}

output "schedule_name" {
  description = "インスタンスを止める EventBridge Scheduler のスケジュール名"
  value       = aws_scheduler_schedule.stop.name
}

output "schedule_expression" {
  description = "スケジュールの式"
  value       = aws_scheduler_schedule.stop.schedule_expression
}
