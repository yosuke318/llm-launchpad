# VRAM見積もりとインスタンス型の選択を、sizing モジュール単体で確かめる。terraform test で実行する
#
# 見積もり(GiB) = パラメータ数 x 1e9 x 0.6 / 2^30
#               + 2 x 層数 x KVヘッド数 x ヘッド次元 x コンテキスト長 x 要素のバイト数 / 2^30
#               + 余裕 1.5

variables {
  models_file         = "../catalog/models.yaml"
  instance_types_file = "../catalog/instance_types.yaml"
  context_length      = 8192
  kv_cache_type       = "f16"
  vram_overhead_gib   = 1.5
}

run "small_model_fits_smallest_type" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model = "qwen3:8b"
  }

  # 重み 8.2 x 0.6 = 4.58 GiB、KV 2 x 36 x 8 x 128 x 8192 x 2 バイト = 1.125 GiB
  assert {
    condition     = output.instance_type == "g4dn.xlarge"
    error_message = "qwen3:8b(8K)は g4dn.xlarge に収まること"
  }

  assert {
    condition     = output.vram_estimate.weights_gib == 4.58 && output.vram_estimate.kv_cache_gib == 1.13 && output.vram_estimate.overhead_gib == 1.5
    error_message = "見積もりの内訳(重み / KVキャッシュ / 余裕)が出力されること"
  }

  assert {
    condition     = output.vram_estimate.required_gib == 7.21 && output.vram_estimate.instance_vram_gib == 15 && output.vram_estimate.headroom_gib == 7.79
    error_message = "合計と、選んだ型のVRAM・残りが出力されること"
  }

  assert {
    condition     = output.model.tag == "qwen3:8b" && output.model.vendor == "Alibaba" && output.model.license == "Apache-2.0"
    error_message = "モデルの定義が出力されること"
  }
}

run "same_vram_prefers_earlier_row" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model = "qwen3:32b"
  }

  # 18.33 + 2.0 + 1.5 = 21.83 GiB。g6.xlarge と g5.xlarge は同じ 22.5 GiB なので表で上の g6 を選ぶ
  assert {
    condition     = output.instance_type == "g6.xlarge"
    error_message = "同じVRAMの型は表の順で選ぶこと"
  }

  assert {
    condition     = output.fitting_instance_types[0] == "g6.xlarge" && output.fitting_instance_types[1] == "g5.xlarge"
    error_message = "収まる型が合計VRAMの昇順、同じVRAMなら表の順に並ぶこと"
  }
}

run "large_model_uses_l40s" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model = "llama3.3:70b"
  }

  # 39.45 + 2.5 + 1.5 = 43.45 GiB
  assert {
    condition     = output.instance_type == "g6e.xlarge"
    error_message = "llama3.3:70b(8K)は g6e.xlarge に収まること"
  }
}

run "longer_context_needs_bigger_type" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model          = "llama3.3:70b"
    context_length = 32768
  }

  # KVキャッシュが 10 GiB に増え、合計 50.95 GiB で 1GPU の型には収まらない
  assert {
    condition     = output.vram_estimate.kv_cache_gib == 10 && output.instance_type == "g6.12xlarge"
    error_message = "コンテキスト長に応じてKVキャッシュが増え、大きい型が選ばれること"
  }
}

run "quantized_kv_cache_reduces_vram" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model          = "llama3.3:70b"
    context_length = 32768
    kv_cache_type  = "q4_0"
  }

  assert {
    condition     = output.vram_estimate.kv_cache_gib == 2.5 && output.instance_type == "g6e.xlarge"
    error_message = "KVキャッシュを量子化すると必要VRAMが減ること"
  }
}

run "multi_gpu_for_moe_model" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model = "gpt-oss:120b"
  }

  # MoE は総パラメータ数で見積もる。65.38 + 0.56 + 1.5 = 67.44 GiB
  assert {
    condition     = output.vram_estimate.required_gib == 67.44 && output.instance_type == "g6.12xlarge" && output.instance.gpu_count == 4
    error_message = "1GPUに収まらないモデルは複数GPUの型を選ぶこと"
  }
}

run "instance_type_override" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model         = "qwen3:8b"
    instance_type = "g5.xlarge"
  }

  assert {
    condition     = output.instance_type == "g5.xlarge" && output.vram_estimate.instance_vram_gib == 22.5
    error_message = "instance_type を指定したらその型を使うこと"
  }
}

run "warns_when_override_does_not_fit" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model         = "llama3.3:70b"
    instance_type = "g4dn.xlarge"
  }

  # 止めずに警告だけ出す
  expect_failures = [check.instance_type_override_fits]
}

run "rejects_unknown_model" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model = "no-such-model:1b"
  }

  expect_failures = [output.instance_type]
}

run "rejects_context_over_model_max" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model          = "qwen3:8b"
    context_length = 65536
  }

  expect_failures = [output.instance_type]
}

run "rejects_unknown_override_type" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model         = "qwen3:8b"
    instance_type = "p5.48xlarge"
  }

  expect_failures = [output.instance_type]
}

run "rejects_when_nothing_fits" {
  command = plan

  module {
    source = "./modules/sizing"
  }

  variables {
    model               = "llama3.3:70b"
    instance_types_file = "tests/fixtures/instance_types_small.yaml"
  }

  expect_failures = [output.instance_type]
}
