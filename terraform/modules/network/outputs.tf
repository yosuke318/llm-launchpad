output "vpc_id" {
  description = "VPCのID"
  value       = aws_vpc.this.id
}

output "subnet_ids" {
  description = "AZ名ごとのパブリックサブネットID"
  value       = { for az, s in aws_subnet.public : az => s.id }
}

output "route_table_id" {
  description = "パブリックルートテーブルのID"
  value       = aws_route_table.public.id
}
