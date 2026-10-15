terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# インラインルールを書かないと、Terraform は AWS が作る既定の全許可アウトバウンドルールを消す。
# ルールは下の個別リソースで必要な分だけ足す
resource "aws_security_group" "instance" {
  name_prefix = "${var.name}-"
  description = "GPU instance for ${var.name}"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, { Name = var.name })

  lifecycle {
    create_before_destroy = true
  }
}

# ssm モードでは allowed_cidrs が空になり、インバウンドは一切開けない
resource "aws_vpc_security_group_ingress_rule" "ollama_api" {
  for_each = toset(var.allowed_cidrs)

  security_group_id = aws_security_group.instance.id
  description       = "Ollama API"
  ip_protocol       = "tcp"
  from_port         = var.ollama_port
  to_port           = var.ollama_port
  cidr_ipv4         = each.value

  tags = merge(var.tags, { Name = "${var.name}-ollama-api" })
}

# アウトバウンドは HTTPS(SSM、S3、Ollama library、Hugging Face)と
# HTTP(OSのパッケージミラー)だけ許可する。DNS と時刻同期はVPC内のリンクローカルなので不要
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"

  tags = merge(var.tags, { Name = "${var.name}-https" })
}

resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.instance.id
  description       = "HTTP (package mirrors)"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"

  tags = merge(var.tags, { Name = "${var.name}-http" })
}
