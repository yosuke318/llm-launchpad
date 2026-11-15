# AWSに接続せずに plan の結果を確かめるテスト。terraform/model-store で terraform test を実行する

mock_provider "aws" {}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "123456789012"
  }
}

run "bucket_name_follows_naming_rule" {
  command = plan

  # 環境側(terraform/main.tf)が探す名前と同じであること
  assert {
    condition     = output.bucket_name == "llm-launchpad-models-123456789012-ap-northeast-1"
    error_message = "バケット名は llm-launchpad-models-<アカウントID>-<リージョン> であること"
  }

  assert {
    condition     = length(aws_s3_bucket.models) == 1 && length(aws_ebs_volume.models) == 0
    error_message = "既定ではバケットだけを作ること"
  }
}

run "creates_model_volumes" {
  command = plan

  variables {
    create_bucket = false
    model_volumes = {
      default = {
        availability_zone = "ap-northeast-1a"
        size_gib          = 200
      }
    }
  }

  assert {
    condition     = length(aws_s3_bucket.models) == 0
    error_message = "create_bucket = false ならバケットを作らないこと"
  }

  assert {
    condition     = aws_ebs_volume.models["default"].tags["ModelVolume"] == "default" && aws_ebs_volume.models["default"].encrypted && aws_ebs_volume.models["default"].throughput == 250
    error_message = "ボリュームに ModelVolume タグを付け、暗号化し、既定のスループットを 250 MiB/s にすること"
  }
}

run "rejects_invalid_volume_name" {
  command = plan

  variables {
    model_volumes = {
      "Bad_Name" = {
        availability_zone = "ap-northeast-1a"
        size_gib          = 200
      }
    }
  }

  expect_failures = [var.model_volumes]
}

run "versioning_is_opt_in" {
  command = plan

  assert {
    condition     = length(aws_s3_bucket_versioning.models) == 0 && output.versioning_enabled == false
    error_message = "既定ではバージョニングを有効にしないこと(古い版の保管料金がかかるため)"
  }
}

run "versioning_for_s3_files" {
  command = plan

  variables {
    enable_versioning = true
  }

  assert {
    condition     = aws_s3_bucket_versioning.models[0].versioning_configuration[0].status == "Enabled" && output.versioning_enabled
    error_message = "S3 Files のためにバージョニングを有効にすること"
  }

  assert {
    condition     = one([for r in aws_s3_bucket_lifecycle_configuration.models[0].rule : r.noncurrent_version_expiration[0].noncurrent_days if r.id == "expire-noncurrent-versions"]) == 1
    error_message = "古い版は1日で消すこと"
  }
}
