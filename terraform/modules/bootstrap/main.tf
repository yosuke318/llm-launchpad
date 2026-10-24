terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# 起動処理の進み具合。インスタンスが書き換えるため、Terraform は作るときの値だけを持つ
resource "aws_ssm_parameter" "status" {
  name        = var.status_parameter_name
  description = "Bootstrap status of ${var.name}"
  type        = "String"
  tier        = "Standard"
  value       = jsonencode({ stage = "pending", state = "running" })

  tags = merge(var.tags, { Name = var.name })

  lifecycle {
    ignore_changes = [value]
  }
}

locals {
  user_data = templatefile("${path.module}/templates/user_data.sh.tftpl", {
    region           = var.region
    status_parameter = aws_ssm_parameter.status.name
    launchpad_py     = trimsuffix(file("${path.module}/files/launchpad.py"), "\n")
    model            = var.model
    context_length   = var.context_length
    kv_cache_type    = var.kv_cache_type
    ollama_version   = var.ollama_version == null ? "" : var.ollama_version
    ollama_host      = var.ollama_listen_all ? "0.0.0.0" : "127.0.0.1"
    ollama_port      = var.ollama_port
  })
}
