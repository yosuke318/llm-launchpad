output "vpc_id" {
  description = "VPCのID"
  value       = aws_vpc.this.id
}

output "subnet_ids" {
  description = "AZ名ごとのサブネットID"
  value       = { for az, s in aws_subnet.this : az => s.id }
}

output "route_table_id" {
  description = "ルートテーブルのID"
  value       = aws_route_table.this.id
}

output "vpc_endpoint_ids" {
  description = "SSM 接続用のインターフェイスエンドポイントのID(閉じたネットワークだけ)"
  value       = [for e in aws_vpc_endpoint.interface : e.id]
}

output "vpc_endpoints" {
  description = "SSM 接続用のインターフェイスエンドポイント。サービス名ごとの置いたサブネットとプライベートDNSの有無"
  value = {
    for service, e in aws_vpc_endpoint.interface : service => {
      subnet_ids          = e.subnet_ids
      private_dns_enabled = e.private_dns_enabled
    }
  }
}

output "internet_gateway_id" {
  description = "インターネットゲートウェイのID(閉じたネットワークでは null)"
  value       = try(aws_internet_gateway.this[0].id, null)
}

output "public_ip_on_launch" {
  description = "サブネットで起動したインスタンスにパブリックIPを付けるか"
  value       = alltrue([for s in aws_subnet.this : s.map_public_ip_on_launch])
}
