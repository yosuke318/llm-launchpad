# GPUインスタンスのAZ選択と入力の検証を、instance モジュール単体で確かめる。terraform test で実行する

mock_provider "aws" {}

override_data {
  target = data.aws_ami.dlami
  values = {
    id               = "ami-0123456789abcdef0"
    name             = "Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 24.04) 20261016"
    root_device_name = "/dev/sda1"
    block_device_mappings = [
      {
        device_name  = "/dev/sda1"
        ebs          = { volume_size = "75" }
        no_device    = ""
        virtual_name = ""
      },
    ]
  }
}

override_data {
  target = data.aws_ec2_instance_type_offerings.this
  values = {
    locations = ["ap-northeast-1c", "ap-northeast-1a"]
  }
}

variables {
  name            = "llm-launchpad-test"
  instance_type   = "g6.xlarge"
  purchase_option = "spot"
  subnet_ids = {
    "ap-northeast-1a" = "subnet-aaaa"
    "ap-northeast-1c" = "subnet-cccc"
    "ap-northeast-1d" = "subnet-dddd"
  }
  security_group_id     = "sg-0123"
  instance_profile_name = "llm-lp-test"
  model_file_size_gb    = 20
  user_data             = "#!/bin/bash\ntrue\n"
}

run "picks_first_offered_az" {
  command = plan

  module {
    source = "./modules/instance"
  }

  # AMI 75 GiB + 20GB x 2 + 10 GiB
  assert {
    condition     = output.availability_zone == "ap-northeast-1a" && output.root_volume_size_gib == 125
    error_message = "提供されているAZのうち最初のAZを選び、ルートボリュームを AMI + モデル x 2 + 10 GiB にすること"
  }
}

run "rejects_az_without_offering" {
  command = plan

  module {
    source = "./modules/instance"
  }

  variables {
    availability_zone = "ap-northeast-1d"
  }

  expect_failures = [aws_instance.this]
}

run "rejects_az_without_subnet" {
  command = plan

  module {
    source = "./modules/instance"
  }

  variables {
    availability_zone = "ap-northeast-1b"
  }

  expect_failures = [aws_instance.this]
}

run "rejects_type_not_offered_in_any_subnet_az" {
  command = plan

  module {
    source = "./modules/instance"
  }

  override_data {
    target = data.aws_ec2_instance_type_offerings.this
    values = {
      locations = ["ap-northeast-1b"]
    }
  }

  expect_failures = [aws_instance.this]
}

run "rejects_volume_smaller_than_ami" {
  command = plan

  module {
    source = "./modules/instance"
  }

  variables {
    root_volume_size_gib = 50
  }

  expect_failures = [aws_instance.this]
}
