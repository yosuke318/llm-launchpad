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

  # AZごとに /+8 のパブリックサブネットを切る
  subnet_cidrs = { for i, az in local.azs : az => cidrsubnet(var.vpc_cidr, 8, i) }
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

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = var.name })
}

# NATゲートウェイは時間課金があるため使わず、パブリックサブネット + パブリックIPで外に出る。
# インバウンドはセキュリティグループで閉じる
resource "aws_subnet" "public" {
  for_each = local.subnet_cidrs

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.key
  cidr_block              = each.value
  map_public_ip_on_launch = true

  tags = merge(var.tags, { Name = "${var.name}-public-${each.key}" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = merge(var.tags, { Name = "${var.name}-public" })
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  for_each = aws_subnet.public

  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

# S3 のゲートウェイエンドポイントは無料。モデル取得(YOSUKE-172)の通信をVPC内で完結させる
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]

  tags = merge(var.tags, { Name = "${var.name}-s3" })
}
