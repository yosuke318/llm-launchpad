output "security_group_id" {
  description = "セキュリティグループのID"
  value       = aws_security_group.instance.id
}

output "allowed_cidrs" {
  description = "Ollama API への接続を許可しているCIDR"
  value       = sort([for r in aws_vpc_security_group_ingress_rule.ollama_api : r.cidr_ipv4])
}

output "http_egress_enabled" {
  description = "HTTP のアウトバウンドを開けているか(閉じたネットワークでは false)"
  value       = length(aws_vpc_security_group_egress_rule.http) > 0
}
