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

variables {
  model = "qwen3:8b"
}

override_data {
  target = module.network.data.aws_availability_zones.available
  values = {
    names = ["ap-northeast-1d", "ap-northeast-1a", "ap-northeast-1c"]
  }
}

override_data {
  target = module.instance.data.aws_ami.dlami
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

# g4dn.xlarge(qwen3:8b で選ばれる型)は 1a と 1c だけで提供されている、という想定
override_data {
  target = module.instance.data.aws_ec2_instance_type_offerings.this
  values = {
    locations = ["ap-northeast-1c", "ap-northeast-1a"]
  }
}

override_resource {
  target = module.instance.aws_instance.this
  values = {
    id        = "i-0123456789abcdef0"
    public_ip = "198.51.100.20"
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

  assert {
    condition     = output.instance_type == "g4dn.xlarge" && output.vram_estimate.context_length == 8192
    error_message = "モデル定義から見積もったインスタンス型が出力されること"
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

run "spot_instance_by_default" {
  # スポットの設定は plan の時点では値が決まらないため、mock で apply して確かめる
  command = apply

  assert {
    condition     = output.purchase_option == "spot" && module.instance.spot_options.instance_interruption_behavior == "stop" && module.instance.spot_options.spot_instance_type == "persistent"
    error_message = "既定はスポットで、中断時は停止すること"
  }

  assert {
    condition     = output.availability_zone == "ap-northeast-1a"
    error_message = "インスタンス型を提供しているAZのうち、名前の昇順で最初のAZを選ぶこと"
  }

  # AMI 75 GiB + qwen3:8b の 5.2GB x 2 を切り上げた 11 GiB + 余裕 10 GiB
  assert {
    condition     = output.root_volume_size_gib == 96
    error_message = "ルートボリュームの既定は AMI + モデル x 2 + 10 GiB であること"
  }
}

run "on_demand_has_no_spot_options" {
  command = apply

  variables {
    purchase_option = "on-demand"
  }

  assert {
    condition     = module.instance.spot_options == null
    error_message = "on-demand ではスポットの設定を付けないこと"
  }
}

run "explicit_az_and_volume_size" {
  command = plan

  variables {
    availability_zone    = "ap-northeast-1c"
    root_volume_size_gib = 200
  }

  assert {
    condition     = output.availability_zone == "ap-northeast-1c" && output.root_volume_size_gib == 200
    error_message = "指定したAZとボリュームの大きさを使うこと"
  }
}

run "connection_info_for_ssm" {
  command = apply

  assert {
    condition     = output.ollama_endpoint == "http://localhost:11434"
    error_message = "ssm モードではポートフォワード先の localhost をエンドポイントにすること"
  }

  assert {
    condition     = output.ssm_port_forward_command == "aws ssm start-session --target i-0123456789abcdef0 --region ap-northeast-1 --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"11434\"],\"localPortNumber\":[\"11434\"]}'"
    error_message = "SSM のポートフォワードのコマンドを出すこと"
  }

  assert {
    condition     = strcontains(output.curl_example, "http://localhost:11434/api/generate") && strcontains(output.curl_example, "\"model\":\"qwen3:8b\"")
    error_message = "curl の例にエンドポイントとモデル名が入ること"
  }

  assert {
    condition     = strcontains(output.openai_api_example, "http://localhost:11434/v1/chat/completions")
    error_message = "OpenAI 互換API の例を出すこと"
  }
}

run "connection_info_for_direct" {
  command = apply

  variables {
    access_mode   = "direct"
    allowed_cidrs = ["198.51.100.7/32"]
    aws_profile   = "my-profile"
  }

  assert {
    condition     = output.ollama_endpoint == "http://198.51.100.20:11434" && output.ssm_port_forward_command == null
    error_message = "direct モードではパブリックIPに直接つなぐこと"
  }

  assert {
    condition     = output.ssm_session_command == "aws ssm start-session --target i-0123456789abcdef0 --region ap-northeast-1 --profile my-profile"
    error_message = "aws_profile を指定したらコマンドに --profile を付けること"
  }
}

run "rejects_unknown_purchase_option" {
  command = plan

  variables {
    purchase_option = "reserved"
  }

  expect_failures = [var.purchase_option]
}

run "bootstrap_status_parameter_and_command" {
  command = apply

  assert {
    condition     = output.status_parameter_name == "/llm-launchpad/default/status"
    error_message = "状態を書くパラメータは /llm-launchpad/<環境ID>/status であること"
  }

  assert {
    condition     = output.status_command == "aws ssm get-parameter --name /llm-launchpad/default/status --region ap-northeast-1 --query Parameter.Value --output text"
    error_message = "状態を読むコマンドを出すこと"
  }
}

run "user_data_for_ssm_mode" {
  command = plan

  assert {
    condition     = strcontains(module.bootstrap.user_data, "OLLAMA_HOST=127.0.0.1:11434")
    error_message = "ssm モードでは Ollama を 127.0.0.1 だけで待ち受けること"
  }

  assert {
    condition     = strcontains(module.bootstrap.user_data, "lp pull \"qwen3:8b\"") && strcontains(module.bootstrap.user_data, "OLLAMA_CONTEXT_LENGTH=8192")
    error_message = "モデルとコンテキスト長を user data に渡すこと"
  }

  assert {
    condition     = !strcontains(module.bootstrap.user_data, "OLLAMA_FLASH_ATTENTION") && strcontains(module.bootstrap.user_data, "curl -fsSL https://ollama.com/install.sh | sh")
    error_message = "f16 では Flash Attention を指定せず、バージョン未指定なら最新の Ollama を入れること"
  }

  assert {
    condition     = strcontains(module.bootstrap.user_data, "LAUNCHPAD_STATUS_PARAMETER=\"/llm-launchpad/default/status\"")
    error_message = "状態を書くパラメータ名を user data に渡すこと"
  }
}

run "user_data_for_direct_mode_and_options" {
  command = plan

  variables {
    access_mode    = "direct"
    allowed_cidrs  = ["198.51.100.7/32"]
    kv_cache_type  = "q8_0"
    ollama_version = "0.12.6"
  }

  assert {
    condition     = strcontains(module.bootstrap.user_data, "OLLAMA_HOST=0.0.0.0:11434")
    error_message = "direct モードでは全てのアドレスで待ち受けること"
  }

  assert {
    condition     = strcontains(module.bootstrap.user_data, "OLLAMA_KV_CACHE_TYPE=q8_0") && strcontains(module.bootstrap.user_data, "OLLAMA_FLASH_ATTENTION=1")
    error_message = "KVキャッシュを量子化するときは Flash Attention を有効にすること"
  }

  assert {
    condition     = strcontains(module.bootstrap.user_data, "OLLAMA_VERSION=\"0.12.6\" sh")
    error_message = "ollama_version を指定したらそのバージョンを入れること"
  }
}

run "user_data_fits_size_limit" {
  command = plan

  variables {
    model = "nemotron-3-super:120b"
  }

  # EC2 の user data は 16KB まで
  assert {
    condition     = length(module.bootstrap.user_data) < 16384
    error_message = "user data は 16KB 未満であること"
  }
}

run "rejects_invalid_ollama_version" {
  command = plan

  variables {
    ollama_version = "latest"
  }

  expect_failures = [var.ollama_version]
}
