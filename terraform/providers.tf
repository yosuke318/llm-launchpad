provider "aws" {
  region  = var.region
  profile = var.aws_profile

  # 消し忘れ検出で使う管理用タグ。全リソースに付く
  default_tags {
    tags = local.default_tags
  }
}
