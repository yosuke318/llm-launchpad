provider "aws" {
  region  = var.region
  profile = var.aws_profile

  # 消し忘れ検出で使う管理用タグ。全リソースに付く
  default_tags {
    tags = local.default_tags
  }
}

# GPUインスタンスを作るときだけ使う。在庫がないAZでは RunInstances が InsufficientInstanceCapacity を返し、
# aws provider はそれを最大 max_retries 回(既定25回、間隔は最長300秒)繰り返すため、apply が何時間も終わらない。
# インスタンスの timeouts は RunInstances の再試行には効かないので、回数を減らして数十秒で失敗させ、別のAZを選べるようにする
provider "aws" {
  alias       = "instance"
  region      = var.region
  profile     = var.aws_profile
  max_retries = 5

  default_tags {
    tags = local.default_tags
  }
}
