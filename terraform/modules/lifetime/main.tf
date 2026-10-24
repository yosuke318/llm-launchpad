# 最大稼働時間。インスタンスを起動してから決まった時間がたったら、EventBridge Scheduler が外から止める。
# インスタンスの中の仕組み(アイドル停止)が動いていなくても止まるようにするため、インスタンスの外に置く

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
    time = {
      source = "hashicorp/time"
    }
  }
}

data "aws_caller_identity" "current" {}

# インスタンスを作り直したら、そこから数え直す
resource "time_static" "launched" {
  triggers = {
    instance_id = var.instance_id
  }
}

locals {
  stop_at = timeadd(time_static.launched.rfc3339, "${var.max_lifetime_hours}h")
}

resource "aws_iam_role" "scheduler" {
  name_prefix = var.name_prefix
  description = "EventBridge Scheduler stops the GPU instance after its max lifetime"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
      }
    }]
  })

  tags = var.tags
}

# この環境のインスタンスを止めることだけを許す
resource "aws_iam_role_policy" "stop_instance" {
  name = "stop-instance"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ec2:StopInstances"]
      Resource = var.instance_arn
    }]
  })
}

resource "aws_scheduler_schedule" "stop" {
  name_prefix = var.name_prefix
  description = "Stop ${var.instance_id} after ${var.max_lifetime_hours} hours"

  # 1回だけ動く。時刻は UTC で書く
  schedule_expression          = "at(${formatdate("YYYY-MM-DD'T'hh:mm:ss", local.stop_at)})"
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:stopInstances"
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ InstanceIds = [var.instance_id] })

    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 10
    }
  }
}
