terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# オプトインが必要なAZ(Local Zone など)は除く
data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  azs = slice(sort(data.aws_availability_zones.available.names), 0, min(var.az_count, length(data.aws_availability_zones.available.names)))

  # AZごとに /+8 のサブネットを切る
  subnet_cidrs = { for i, az in local.azs : az => cidrsubnet(var.vpc_cidr, 8, i) }

  subnet_kind = var.private ? "private" : "public"
}

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr

  # SSM エージェントがエンドポイントを名前解決するために必要
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(var.tags, { Name = var.name })
}

# 既定のセキュリティグループは使わないため、ルールを空にしておく
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-default-unused" })
}

# 閉じたネットワーク(private)では、インターネットゲートウェイを作らない
resource "aws_internet_gateway" "this" {
  count = var.private ? 0 : 1

  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = var.name })
}

# NATゲートウェイは時間課金があるため使わず、パブリックサブネット + パブリックIPで外に出る。
# インバウンドはセキュリティグループで閉じる。
# 閉じたネットワーク(private)では、パブリックIPを付けず、外への経路もない
resource "aws_subnet" "this" {
  for_each = local.subnet_cidrs

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = !var.private

  tags = merge(var.tags, { Name = "${var.name}-${local.subnet_kind}-${each.key}" })
}

resource "aws_route_table" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-${local.subnet_kind}" })
}

resource "aws_route" "internet" {
  count = var.private ? 0 : 1

  route_table_id         = aws_route_table.this.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this[0].id
}

resource "aws_route_table_association" "this" {
  for_each = aws_subnet.this

  subnet_id      = each.value.id
  route_table_id = aws_route_table.this.id
}

# 閉じたネットワークを足す前に作った環境を、作り直さずに引き継ぐ
moved {
  from = aws_internet_gateway.this
  to   = aws_internet_gateway.this[0]
}

moved {
  from = aws_subnet.public
  to   = aws_subnet.this
}

moved {
  from = aws_route_table.public
  to   = aws_route_table.this
}

moved {
  from = aws_route.internet
  to   = aws_route.internet[0]
}

moved {
  from = aws_route_table_association.public
  to   = aws_route_table_association.this
}

# S3 のゲートウェイエンドポイントは無料。S3からのモデル取得の通信をVPC内で完結させる。
# 閉じたネットワークでは、S3 への経路はこれだけになる
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.this.id]

  tags = merge(var.tags, { Name = "${var.name}-s3" })
}

# SSM 接続用のインターフェイスエンドポイント(閉じたネットワークだけ)。1つあたりAZごとに時間課金があるため、
# インスタンスを起動するAZのサブネットにだけ置く。プライベートDNSで、SSM エージェントは既定の名前のまま使える
resource "aws_security_group" "endpoints" {
  count = var.private ? 1 : 0

  name_prefix = "${var.name}-endpoints-"
  description = "VPC endpoints for ${var.name}"
  vpc_id      = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-endpoints" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_https" {
  count = var.private ? 1 : 0

  security_group_id = aws_security_group.endpoints[0].id
  description       = "HTTPS from the VPC"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = var.vpc_cidr

  tags = merge(var.tags, { Name = "${var.name}-endpoints-https" })
}

resource "aws_vpc_endpoint" "interface" {
  for_each = var.private ? toset(local.interface_endpoint_services) : toset([])

  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.this[var.endpoint_availability_zone].id]
  security_group_ids  = [aws_security_group.endpoints[0].id]
  private_dns_enabled = true

  tags = merge(var.tags, { Name = "${var.name}-${each.key}" })

  lifecycle {
    precondition {
      condition     = contains(keys(local.subnet_cidrs), coalesce(var.endpoint_availability_zone, "-"))
      error_message = "閉じたネットワークでは、エンドポイントを置くAZ(${coalesce(var.endpoint_availability_zone, "未定")})にサブネットが要ります。"
    }
  }
}

locals {
  # ssm: 状態の書き込みと SSM エージェントの登録 / ec2messages と ssmmessages: Session Manager の接続
  interface_endpoint_services = ["ssm", "ec2messages", "ssmmessages"]
}
