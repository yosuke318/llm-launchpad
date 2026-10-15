# AWSに接続せずに plan の結果を確かめるテスト。terraform test で実行する

mock_provider "aws" {
  # ARN の検証を通すため、パーティションは実在の値にする
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }
}
mock_provider "http" {}
mock_provider "time" {}

override_data {
  target = module.network.data.aws_availability_zones.available
  values = {
    names = ["ap-northeast-1d", "ap-northeast-1a", "ap-northeast-1c"]
  }
}

override_data {
  target = data.http.my_ip
  values = {
    status_code   = 200
    response_body = "203.0.113.10\n"
  }
}

run "defaults_use_ssm_without_ingress" {
  command = plan

  assert {
    condition     = output.access_mode == "ssm"
    error_message = "既定の接続方法は ssm であるべき"
  }

  assert {
    condition     = length(output.allowed_cidrs) == 0
    error_message = "ssm モードではインバウンドを開けないこと"
  }

  assert {
    condition     = output.env_id == "default"
    error_message = "env_id 未指定なら workspace 名を使うこと"
  }

  assert {
    condition     = output.tags["Project"] == "llm-launchpad" && output.tags["EnvId"] == "default" && output.tags["ManagedBy"] == "terraform"
    error_message = "管理用タグが付いていること"
  }

  assert {
    condition     = output.region == "ap-northeast-1"
    error_message = "既定リージョンは東京であること"
  }
}

run "subnets_are_created_per_az_in_sorted_order" {
  command = plan

  variables {
    az_count = 2
  }

  assert {
    condition     = join(",", keys(output.subnet_ids)) == "ap-northeast-1a,ap-northeast-1c"
    error_message = "AZ名の昇順で az_count 個のサブネットを作ること"
  }
}

run "az_count_is_capped_by_available_azs" {
  command = plan

  variables {
    az_count = 6
  }

  assert {
    condition     = length(output.subnet_ids) == 3
    error_message = "AZ数より多い az_count はAZ数に合わせること"
  }
}

run "direct_mode_uses_given_cidrs" {
  command = plan

  variables {
    access_mode   = "direct"
    allowed_cidrs = ["198.51.100.7/32", "192.0.2.0/24"]
  }

  assert {
    condition     = output.allowed_cidrs == tolist(["192.0.2.0/24", "198.51.100.7/32"])
    error_message = "指定したCIDRだけを許可すること"
  }
}

run "direct_mode_detects_my_ip_when_cidrs_empty" {
  command = plan

  variables {
    access_mode = "direct"
  }

  assert {
    condition     = output.allowed_cidrs == tolist(["203.0.113.10/32"])
    error_message = "allowed_cidrs が空なら自分のグローバルIP/32を許可すること"
  }
}

run "ssm_mode_ignores_allowed_cidrs" {
  command = plan

  variables {
    access_mode   = "ssm"
    allowed_cidrs = ["198.51.100.7/32"]
  }

  assert {
    condition     = length(output.allowed_cidrs) == 0
    error_message = "ssm モードでは allowed_cidrs を指定してもインバウンドを開けないこと"
  }
}

run "explicit_env_id_is_used_for_tags" {
  command = plan

  variables {
    env_id = "qwen-test"
    extra_tags = {
      Owner   = "me"
      Project = "overridden"
    }
  }

  assert {
    condition     = output.env_id == "qwen-test" && output.tags["EnvId"] == "qwen-test"
    error_message = "env_id を指定したらそれを使うこと"
  }

  assert {
    condition     = output.tags["Owner"] == "me" && output.tags["Project"] == "llm-launchpad"
    error_message = "extra_tags は追加できるが管理用タグは上書きできないこと"
  }
}

run "model_bucket_read_policy_is_optional" {
  command = plan

  assert {
    condition     = module.iam.model_bucket_read_enabled == false
    error_message = "バケット未指定なら読み取り権限を付けないこと"
  }
}

run "model_bucket_read_policy_when_bucket_given" {
  command = plan

  variables {
    model_bucket_name = "llm-launchpad-models-example"
  }

  assert {
    condition     = module.iam.model_bucket_read_enabled == true
    error_message = "バケットを指定したら読み取り権限を付けること"
  }
}

run "rejects_open_to_world" {
  command = plan

  variables {
    access_mode   = "direct"
    allowed_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.allowed_cidrs]
}

run "rejects_non_network_address_cidr" {
  command = plan

  variables {
    access_mode   = "direct"
    allowed_cidrs = ["192.0.2.5/24"]
  }

  expect_failures = [var.allowed_cidrs]
}

run "rejects_invalid_cidr" {
  command = plan

  variables {
    access_mode   = "direct"
    allowed_cidrs = ["203.0.113.10"]
  }

  expect_failures = [var.allowed_cidrs]
}

run "rejects_unknown_access_mode" {
  command = plan

  variables {
    access_mode = "public"
  }

  expect_failures = [var.access_mode]
}

run "rejects_too_small_vpc_cidr" {
  command = plan

  variables {
    vpc_cidr = "10.42.0.0/26"
  }

  expect_failures = [var.vpc_cidr]
}

run "rejects_invalid_env_id" {
  command = plan

  variables {
    env_id = "Invalid_Env"
  }

  expect_failures = [time_static.created]
}
