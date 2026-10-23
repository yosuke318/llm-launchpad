locals {
  # 小数第2位で丸める
  round2 = { for k, v in {
    weights_gib       = local.weights_gib
    kv_cache_gib      = local.kv_cache_gib
    overhead_gib      = var.vram_overhead_gib
    required_gib      = local.required_gib
    instance_vram_gib = local.selected_vram_gib
    headroom_gib      = local.selected_vram_gib - local.required_gib
  } : k => floor(v * 100 + 0.5) / 100 }
}

output "instance_type" {
  description = "選んだインスタンス型"
  value       = local.selected.type

  precondition {
    condition     = local.model_found
    error_message = "model \"${var.model}\" はモデル定義にありません。使えるモデル: ${join(", ", sort(keys(local.catalog.models)))}"
  }

  precondition {
    condition     = !local.model_found || var.context_length <= local.model.max_context
    error_message = "context_length ${var.context_length} は、${var.model} の最大コンテキスト長 ${local.model.max_context} を超えています。"
  }

  precondition {
    condition     = var.instance_type == null || local.override_type != null
    error_message = "instance_type \"${coalesce(var.instance_type, "-")}\" はインスタンス型の表にありません。使える型: ${join(", ", [for t in local.instance_types : t.type])}"
  }

  precondition {
    condition     = local.selected != null
    error_message = "見積もり ${format("%.2f", local.required_gib)} GiB が収まるインスタンス型がありません。context_length を減らすか、kv_cache_type を q8_0 / q4_0 にしてください。"
  }
}

output "instance" {
  description = "選んだインスタンス型の仕様"
  value       = local.selected
}

output "model" {
  description = "モデルの定義"
  value       = merge(local.model, { tag = var.model })
}

output "vram_estimate" {
  description = "必要VRAMの見積もりの内訳(GiB)"
  value = merge(local.round2, {
    context_length = var.context_length
    kv_cache_type  = var.kv_cache_type
  })
}

output "fitting_instance_types" {
  description = "見積もりが収まるインスタンス型(選ばれる順)"
  value       = [for t in local.fitting_types : t.type]
}
