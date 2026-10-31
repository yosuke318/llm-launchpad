# AWSに接続せずに plan の結果を確かめるテスト。terraform/bedrock で terraform test を実行する

mock_provider "aws" {
  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}
mock_provider "archive" {}
mock_provider "time" {}

# 選んでいる workspace に左右されないよう、環境IDを決めておく
variables {
  env_id = "test"
}

override_data {
  target = data.aws_availability_zones.available
  values = {
    names = ["ap-northeast-1d", "ap-northeast-1a", "ap-northeast-1c"]
  }
}

override_data {
  target = module.network.data.aws_availability_zones.available
  values = {
    names = ["ap-northeast-1d", "ap-northeast-1a", "ap-northeast-1c"]
  }
}

# bedrock-runtime のエンドポイントは 1c と 1d にだけ置ける、という想定
override_data {
  target = data.aws_vpc_endpoint_service.bedrock_runtime
  values = {
    availability_zones = ["ap-northeast-1d", "ap-northeast-1c"]
  }
}

override_data {
  target = data.aws_bedrock_inference_profile.this
  values = {
    inference_profile_arn = "arn:aws:bedrock:ap-northeast-1:123456789012:inference-profile/jp.anthropic.claude-haiku-4-5-20251001-v1:0"
    models = [
      { model_arn = "arn:aws:bedrock:ap-northeast-3::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0" },
      { model_arn = "arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0" },
    ]
  }
}

override_resource {
  target = module.network.aws_vpc_endpoint.interface["bedrock-runtime"]
  values = {
    id = "vpce-0123456789abcdef0"
  }
}

run "closed_network_with_bedrock_endpoint_only" {
  command = plan

  assert {
    condition     = module.network.internet_gateway_id == null && module.network.public_ip_on_launch == false
    error_message = "インターネットゲートウェイを作らず、パブリックIPも付けないこと"
  }

  assert {
    condition     = keys(module.network.vpc_endpoints) == ["bedrock-runtime"]
    error_message = "インターフェイスエンドポイントは bedrock-runtime だけ(SSM 用は作らない)であること"
  }

  # 名前順で最初の 1a はエンドポイントを置けないので、1c になる
  assert {
    condition     = local.availability_zone == "ap-northeast-1c"
    error_message = "エンドポイントを置けるAZのうち、名前順で最初のものを選ぶこと"
  }

  assert {
    condition     = length(one(aws_lambda_function.this.vpc_config).subnet_ids) == 1
    error_message = "Lambda はエンドポイントと同じAZのサブネットにだけ置くこと"
  }

  assert {
    condition     = aws_lambda_function.this.environment[0].variables.MODEL_ID == "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
    error_message = "既定のモデルは Haiku 4.5 の jp. 推論プロファイルであること"
  }

  assert {
    condition     = aws_lambda_function.this.function_name == "llm-launchpad-test-bedrock" && aws_cloudwatch_log_group.lambda.name == "/aws/lambda/llm-launchpad-test-bedrock"
    error_message = "関数名とロググループ名が環境IDから決まること"
  }

  assert {
    condition     = one(aws_vpc_security_group_egress_rule.lambda_https[*].cidr_ipv4) == "10.43.0.0/16" && aws_vpc_security_group_egress_rule.lambda_https.from_port == 443
    error_message = "Lambda の外向きは VPC の中の HTTPS だけであること"
  }

  assert {
    condition     = output.tags.Engine == "bedrock" && output.tags.Project == "llm-launchpad"
    error_message = "消し忘れ検出で区別できるよう Engine = bedrock のタグを付けること"
  }
}

run "invoke_only_through_endpoint_and_profile" {
  command = plan

  assert {
    condition = jsonencode([for s in data.aws_iam_policy_document.bedrock.statement : sort(s.resources)]) == jsonencode([
      ["arn:aws:bedrock:ap-northeast-1:123456789012:inference-profile/jp.anthropic.claude-haiku-4-5-20251001-v1:0"],
      ["arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0", "arn:aws:bedrock:ap-northeast-3::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0"],
    ])
    error_message = "許可するのは選んだ推論プロファイルと、その振り分け先のモデルだけであること"
  }

  assert {
    condition     = [for s in data.aws_iam_policy_document.bedrock.statement : one(s.condition).variable] == ["aws:SourceVpce", "bedrock:InferenceProfileArn"]
    error_message = "推論プロファイルはエンドポイント経由、振り分け先のモデルは推論プロファイル経由に限ること"
  }
}

run "chosen_availability_zone" {
  command = plan

  variables {
    availability_zone = "ap-northeast-1d"
  }

  assert {
    condition     = local.availability_zone == "ap-northeast-1d"
    error_message = "availability_zone を指定したら、そのAZに置くこと"
  }
}

run "model_must_be_in_catalog" {
  command = plan

  variables {
    model = "global.anthropic.claude-sonnet-4-6"
  }

  expect_failures = [data.aws_bedrock_inference_profile.this]
}

run "sonnet_from_catalog" {
  command = plan

  variables {
    model = "jp.anthropic.claude-sonnet-4-6"
  }

  assert {
    condition     = output.model.name == "Claude Sonnet 4.6" && output.model.price_model == "Claude Sonnet 4.6"
    error_message = "カタログにあるモデルを選べること"
  }
}
