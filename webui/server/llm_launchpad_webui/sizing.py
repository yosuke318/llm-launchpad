"""VRAM の見積もりと、インスタンス型の選択。terraform/modules/sizing と同じ計算をする。

画面でモデルを選んだときに、terraform を呼ばずにすぐ推奨のインスタンス型を出すため。
同じ結果になることを tests/test_sizing.py で確かめている。式を変えるときは両方を直す。

    必要VRAM(GiB) = 重み + KVキャッシュ + 余裕
      重み         = 配布サイズ(GB) x 1e9 / 2^30
      KVキャッシュ = 2(K と V) x 注意層の数 x KVヘッド数 x ヘッド次元 x コンテキスト長 x 要素のバイト数 / 2^30
"""

import math
import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

GIB = 1024 ** 3

# KVキャッシュの1要素あたりのバイト数(Ollama の OLLAMA_KV_CACHE_TYPE)
KV_CACHE_BYTES = {"f16": 2, "q8_0": 1, "q4_0": 0.5}

# terraform/variables.tf の既定値
DEFAULT_CONTEXT_LENGTH = 8192
DEFAULT_KV_CACHE_TYPE = "f16"
DEFAULT_VRAM_OVERHEAD_GIB = 1.5


class SizingError(ValueError):
    pass


def round2(value):
    """小数第2位で丸める。terraform の floor(v * 100 + 0.5) / 100 と同じ"""
    return math.floor(value * 100 + 0.5) / 100


@dataclass(frozen=True)
class Catalog:
    models: dict
    instance_types: list

    @classmethod
    def load(cls, catalog_dir):
        with open(os.path.join(catalog_dir, "models.yaml"), encoding="utf-8") as f:
            models = yaml.safe_load(f)["models"]
        with open(os.path.join(catalog_dir, "instance_types.yaml"), encoding="utf-8") as f:
            instance_types = yaml.safe_load(f)["instance_types"]
        return cls(models=models, instance_types=instance_types)

    def sorted_instance_types(self):
        """合計VRAMの昇順。同じVRAMなら表の順(sorted は安定なので、表の順が保たれる)"""
        return sorted(self.instance_types, key=lambda t: t["gpu_count"] * t["vram_gib_per_gpu"])


@lru_cache(maxsize=4)
def load_catalog(catalog_dir):
    return Catalog.load(catalog_dir)


def estimate(catalog, model, context_length=DEFAULT_CONTEXT_LENGTH, kv_cache_type=DEFAULT_KV_CACHE_TYPE,
             vram_overhead_gib=DEFAULT_VRAM_OVERHEAD_GIB):
    """必要VRAMの見積もりと、収まるインスタンス型。terraform の sizing モジュールの出力と同じ形にする"""
    if model not in catalog.models:
        raise SizingError(f"model \"{model}\" はモデル定義にありません")
    if kv_cache_type not in KV_CACHE_BYTES:
        raise SizingError(f"kv_cache_type は {' / '.join(KV_CACHE_BYTES)} のどれかを指定してください")
    m = catalog.models[model]
    if not 512 <= context_length <= m["max_context"]:
        raise SizingError(f"context_length は 512〜{m['max_context']}({model} の最大コンテキスト長)の範囲で指定してください")

    # Mamba や線形注意を混ぜたモデルは、注意層だけがKVキャッシュを持つ
    attention_layers = m.get("attention_layers") or m["layers"]
    weights_gib = m["file_size_gb"] * 1e9 / GIB
    kv_cache_gib = 2 * attention_layers * m["kv_heads"] * m["head_dim"] * context_length * KV_CACHE_BYTES[kv_cache_type] / GIB
    required_gib = weights_gib + kv_cache_gib + vram_overhead_gib

    fitting = [t for t in catalog.sorted_instance_types() if t["gpu_count"] * t["vram_gib_per_gpu"] >= required_gib]
    selected = fitting[0] if fitting else None
    selected_vram_gib = selected["gpu_count"] * selected["vram_gib_per_gpu"] if selected else 0

    return {
        "model": {**m, "tag": model, "attention_layers": attention_layers},
        "instance_type": selected["type"] if selected else None,
        "instance": selected,
        "fitting_instance_types": [t["type"] for t in fitting],
        "vram_estimate": {
            "weights_gib": round2(weights_gib),
            "kv_cache_gib": round2(kv_cache_gib),
            "overhead_gib": round2(vram_overhead_gib),
            "required_gib": round2(required_gib),
            "instance_vram_gib": round2(selected_vram_gib),
            "headroom_gib": round2(selected_vram_gib - required_gib),
            "context_length": context_length,
            "kv_cache_type": kv_cache_type,
        },
    }
