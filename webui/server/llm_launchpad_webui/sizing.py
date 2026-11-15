"""VRAM の見積もりと、インスタンス型の選択。terraform/modules/sizing と同じ計算をする。

画面でモデルを選んだときに、terraform を呼ばずにすぐ推奨のインスタンス型を出すため。
同じ結果になることを tests/test_sizing.py で確かめている。式を変えるときは両方を直す。

    必要VRAM(GiB) = 重み + KVキャッシュ + 余裕
      重み         = 配布サイズ(GB) x 1e9 / 2^30
      KVキャッシュ = 2(K と V) x 注意層の数 x KVヘッド数 x ヘッド次元 x コンテキスト長 x 要素のバイト数 / 2^30

推論エンジンが vLLM のとき(engine = "vllm"):
    重みは bf16 の safetensors(モデル定義の vllm.weights_gb)。KVキャッシュは f16 だけ。
    vLLM は起動のときに GPU のメモリの gpu_memory_utilization(0.9)を先に確保し、その中に重み、
    KVキャッシュ(max_model_len 分)、CUDA グラフなどを置く。収まるかは「合計VRAM x 0.9 >= 必要VRAM」で決める
"""

import math
import os
from dataclasses import dataclass
from functools import lru_cache

import yaml

from .messages import MessageError

GIB = 1024 ** 3

# KVキャッシュの1要素あたりのバイト数(Ollama の OLLAMA_KV_CACHE_TYPE)
KV_CACHE_BYTES = {"f16": 2, "q8_0": 1, "q4_0": 0.5}

# terraform/variables.tf の既定値
DEFAULT_CONTEXT_LENGTH = 8192
DEFAULT_KV_CACHE_TYPE = "f16"
DEFAULT_VRAM_OVERHEAD_GIB = 1.5

ENGINES = ("ollama", "vllm")
# vLLM が GPU のメモリのうち使う割合(--gpu-memory-utilization)と、重みと KV キャッシュのほかに見込む分(CUDA グラフなど)
VLLM_GPU_MEMORY_UTILIZATION = 0.9
VLLM_OVERHEAD_GIB = 2.0


class SizingError(MessageError, ValueError):
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
             vram_overhead_gib=DEFAULT_VRAM_OVERHEAD_GIB, engine="ollama"):
    """必要VRAMの見積もりと、収まるインスタンス型。terraform の sizing モジュールの出力と同じ形にする"""
    if model not in catalog.models:
        raise SizingError("model.unknown", model=model)
    if engine not in ENGINES:
        raise SizingError("engine.invalid", choices=" / ".join(ENGINES))
    if kv_cache_type not in KV_CACHE_BYTES:
        raise SizingError("kv_cache_type.invalid", choices=" / ".join(KV_CACHE_BYTES))
    m = catalog.models[model]
    vllm = engine == "vllm"
    if vllm and not m.get("vllm"):
        raise SizingError("engine.vllm_unsupported", model=model)
    if vllm and kv_cache_type != "f16":
        raise SizingError("engine.vllm_kv_cache", kv_cache_type=kv_cache_type)
    if not 512 <= context_length <= m["max_context"]:
        raise SizingError("context_length.range", max=m["max_context"], model=model)

    # Mamba や線形注意を混ぜたモデルは、注意層だけがKVキャッシュを持つ
    attention_layers = m.get("attention_layers") or m["layers"]
    weights_gib = (m["vllm"]["weights_gb"] if vllm else m["file_size_gb"]) * 1e9 / GIB
    kv_cache_gib = 2 * attention_layers * m["kv_heads"] * m["head_dim"] * context_length * KV_CACHE_BYTES[kv_cache_type] / GIB
    overhead_gib = VLLM_OVERHEAD_GIB if vllm else vram_overhead_gib
    required_gib = weights_gib + kv_cache_gib + overhead_gib
    # vLLM は GPU のメモリの 0.9 だけを使う
    utilization = VLLM_GPU_MEMORY_UTILIZATION if vllm else 1

    fitting = [t for t in catalog.sorted_instance_types() if t["gpu_count"] * t["vram_gib_per_gpu"] * utilization >= required_gib]
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
            "overhead_gib": round2(overhead_gib),
            "required_gib": round2(required_gib),
            "instance_vram_gib": round2(selected_vram_gib),
            # そのエンジンが使える分(vLLM は合計VRAM x 0.9)
            "usable_vram_gib": round2(selected_vram_gib * utilization),
            "headroom_gib": round2(selected_vram_gib * utilization - required_gib),
            "engine": engine,
            "context_length": context_length,
            "kv_cache_type": kv_cache_type,
        },
    }
