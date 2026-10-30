terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

# Deep Learning Base AMI(NVIDIA ドライバと CUDA、SSM エージェントが入っている)のリージョンごとの最新版
data "aws_ami" "dlami" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04) *"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

# インスタンス型を提供しているAZ。GPUの型は提供されていないAZがある
data "aws_ec2_instance_type_offerings" "this" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

locals {
  offered_azs = sort([for az in keys(var.subnet_ids) : az if contains(data.aws_ec2_instance_type_offerings.this.locations, az)])

  availability_zone = var.availability_zone != null ? var.availability_zone : try(local.offered_azs[0], null)

  # AMI のルートボリュームの大きさ(GiB)
  ami_root_size_gib = tonumber(one([
    for b in data.aws_ami.dlami.block_device_mappings : b.ebs.volume_size
    if b.device_name == data.aws_ami.dlami.root_device_name
  ]))

  # AMI の大きさ + モデル2つ分(取得中の一時ファイルや、S3からのコピーの分) + 余裕 10 GiB。
  # モデルを別の EBS ボリュームに置くときは、ルートにはモデルを置かない
  default_root_volume_size_gib = local.ami_root_size_gib + (var.models_on_root_volume ? ceil(var.model_file_size_gb * 2) : 0) + 10

  root_volume_size_gib = coalesce(var.root_volume_size_gib, local.default_root_volume_size_gib)
}

# 閉じたネットワークでは、起動処理が SSM に状態を書くため、エンドポイントができてから起動する
resource "terraform_data" "network_ready" {
  input = var.vpc_endpoint_ids
}

resource "aws_instance" "this" {
  depends_on = [terraform_data.network_ready]

  ami                    = data.aws_ami.dlami.id
  instance_type          = var.instance_type
  subnet_id              = try(var.subnet_ids[local.availability_zone], null)
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = var.instance_profile_name

  # SSH の鍵は使わず、SSM で接続する
  key_name = null

  # user data は初回起動時にしか実行されないため、モデルなどを変えたら作り直す
  user_data_base64            = var.user_data_base64
  user_data_replace_on_change = true

  dynamic "instance_market_options" {
    for_each = var.purchase_option == "spot" ? [1] : []

    content {
      market_type = "spot"

      spot_options {
        # 中断時は停止して、在庫が戻ったら同じディスクのまま再開する。stop は persistent のときだけ指定できる。
        # destroy のときは、aws provider がスポットリクエストをキャンセルしてから終了する
        instance_interruption_behavior = "stop"
        spot_instance_type             = "persistent"
      }
    }
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = local.root_volume_size_gib
    # S3 からのコピーやGPUへの読み込みはディスクの速さで決まる。gp3 の既定(125 MiB/s)では 87GB のモデルに12分ほどかかる
    throughput            = var.root_volume_throughput
    iops                  = var.root_volume_iops
    encrypted             = true
    delete_on_termination = true

    tags = merge(var.tags, { Name = var.name })
  }

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  tags = merge(var.tags, { Name = var.name })

  lifecycle {
    precondition {
      condition     = var.availability_zone != null || length(local.offered_azs) > 0
      error_message = "${var.instance_type} はサブネットのあるAZ(${join(", ", sort(keys(var.subnet_ids)))})で提供されていません。az_count を増やすか、別のリージョンを選んでください。"
    }

    precondition {
      condition     = var.availability_zone == null || contains(keys(var.subnet_ids), coalesce(var.availability_zone, "-"))
      error_message = "availability_zone ${coalesce(var.availability_zone, "-")} にはサブネットがありません。使えるAZ: ${join(", ", sort(keys(var.subnet_ids)))}"
    }

    precondition {
      condition     = var.availability_zone == null || contains(data.aws_ec2_instance_type_offerings.this.locations, coalesce(var.availability_zone, "-"))
      error_message = "${var.instance_type} は ${coalesce(var.availability_zone, "-")} で提供されていません。提供されているAZ: ${join(", ", local.offered_azs)}"
    }

    precondition {
      condition     = local.root_volume_size_gib >= local.ami_root_size_gib
      error_message = "root_volume_size_gib は AMI のルートボリューム(${local.ami_root_size_gib} GiB)以上にしてください。"
    }
  }
}

# EBS永続。ボリュームは model-store で作ったもので、環境を destroy すると付け外しだけをして残す
resource "aws_volume_attachment" "models" {
  count = var.model_volume_id == null ? 0 : 1

  device_name = "/dev/sdf"
  volume_id   = var.model_volume_id
  instance_id = aws_instance.this.id

  # マウントしたまま外すと中身が壊れることがあるため、インスタンスを止めてから外す
  stop_instance_before_detaching = true
}
