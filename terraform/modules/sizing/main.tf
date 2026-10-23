# モデル定義とVRAM見積もりから、GPUインスタンス型を選ぶ。リソースは作らず計算だけを行う

locals {
  catalog        = yamldecode(file(var.models_file))
  instance_types = yamldecode(file(var.instance_types_file)).instance_types

  model_found = contains(keys(local.catalog.models), var.model)

  # カタログにないモデルでも後続の式がエラーにならないよう、空の値で埋めておく(出力の precondition で止める)
  model = merge({
    vendor           = ""
    name             = ""
    params_b         = 0
    quantization     = ""
    file_size_gb     = 0
    max_context      = 0
    layers           = 0
    attention_layers = null
    kv_heads         = 0
    head_dim         = 0
    license          = ""
  }, try(local.catalog.models[var.model], {}))

  # Mamba や線形注意を混ぜたモデルは、注意層だけがKVキャッシュを持つ
  attention_layers = coalesce(local.model.attention_layers, local.model.layers)

  gib = 1024 * 1024 * 1024

  # KVキャッシュの1要素あたりのバイト数(Ollama の OLLAMA_KV_CACHE_TYPE)
  kv_cache_bytes = {
    f16  = 2
    q8_0 = 1
    q4_0 = 0.5
  }

  # 重み: 配布サイズ(GB)を GiB に直す。GGUF はほぼそのままVRAMに載る
  weights_gib = local.model.file_size_gb * 1e9 / local.gib

  # KVキャッシュ: K と V の2つ x 注意層の数 x KVヘッド数 x ヘッド次元 x コンテキスト長 x 要素のバイト数
  kv_cache_gib = 2 * local.attention_layers * local.model.kv_heads * local.model.head_dim * var.context_length * local.kv_cache_bytes[var.kv_cache_type] / local.gib

  required_gib = local.weights_gib + local.kv_cache_gib + var.vram_overhead_gib

  # 合計VRAMの昇順に並べる。同じVRAMなら表の順(sort は文字列の昇順なので桁をそろえる)
  sort_keys    = [for i, t in local.instance_types : format("%010.2f-%03d", t.gpu_count * t.vram_gib_per_gpu, i)]
  sorted_types = [for k in sort(local.sort_keys) : local.instance_types[tonumber(split("-", k)[1])]]

  fitting_types = [for t in local.sorted_types : t if t.gpu_count * t.vram_gib_per_gpu >= local.required_gib]

  override_type = var.instance_type == null ? null : one([for t in local.instance_types : t if t.type == var.instance_type])

  selected = var.instance_type != null ? local.override_type : try(local.fitting_types[0], null)

  selected_vram_gib = try(local.selected.gpu_count * local.selected.vram_gib_per_gpu, 0)
}

# instance_type を手で指定したときは、収まらなくても止めずに警告だけ出す(実測で確かめたい場合があるため)
check "instance_type_override_fits" {
  assert {
    condition     = var.instance_type == null || local.override_type == null || local.selected_vram_gib >= local.required_gib
    error_message = "指定した instance_type (${coalesce(var.instance_type, "-")}) のVRAM ${local.selected_vram_gib} GiB は、見積もり ${format("%.2f", local.required_gib)} GiB に足りません。"
  }
}
