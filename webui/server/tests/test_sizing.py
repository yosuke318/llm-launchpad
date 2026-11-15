"""VRAM の見積もりのテスト。terraform の sizing モジュールと同じ結果になるかも確かめる

    cd webui/server && uv run pytest tests/test_sizing.py
"""

import json
import os
import shutil
import subprocess

import pytest

from llm_launchpad_webui.sizing import SizingError, estimate, load_catalog

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CATALOG_DIR = os.path.join(REPO_ROOT, "catalog")
SIZING_MODULE = os.path.join(REPO_ROOT, "terraform", "modules", "sizing")

CONTEXT_LENGTHS = [4096, 8192, 32768, 131072]
KV_CACHE_TYPES = ["f16", "q8_0"]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_DIR)


def test_small_model(catalog):
    r = estimate(catalog, "qwen3:8b")
    assert r["instance_type"] == "g4dn.xlarge"
    assert r["vram_estimate"] == {
        "weights_gib": 4.84,
        "kv_cache_gib": 1.13,
        "overhead_gib": 1.5,
        "required_gib": 7.47,
        "instance_vram_gib": 15,
        "usable_vram_gib": 15,
        "headroom_gib": 7.53,
        "context_length": 8192,
        "kv_cache_type": "f16",
        "engine": "ollama",
    }
    assert r["model"]["vendor"] == "Alibaba" and r["model"]["tag"] == "qwen3:8b"


def test_longer_context_needs_bigger_type(catalog):
    assert estimate(catalog, "llama3.3:70b", 8192)["instance_type"] == "g6e.xlarge"
    assert estimate(catalog, "llama3.3:70b", 32768)["instance_type"] == "g6.12xlarge"
    assert estimate(catalog, "llama3.3:70b", 32768, "q4_0")["instance_type"] == "g6e.xlarge"


def test_hybrid_model_counts_only_attention_layers(catalog):
    r = estimate(catalog, "nemotron-3-super:120b")
    assert r["model"]["attention_layers"] == 8
    assert r["vram_estimate"]["kv_cache_gib"] == 0.06


def test_same_vram_prefers_earlier_row(catalog):
    r = estimate(catalog, "qwen3:32b")
    assert r["fitting_instance_types"][:2] == ["g6.xlarge", "g5.xlarge"]


@pytest.mark.parametrize("kwargs", [
    {"model": "no-such:1b"},
    {"model": "qwen3:8b", "context_length": 65536},
    {"model": "qwen3:8b", "context_length": 100},
    {"model": "qwen3:8b", "kv_cache_type": "q2"},
])
def test_rejects_invalid_input(catalog, kwargs):
    with pytest.raises(SizingError):
        estimate(catalog, **kwargs)


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform が見つからない")
def test_matches_terraform_sizing_module(catalog, tmp_path):
    """カタログの全モデル x コンテキスト長 x KVキャッシュの型を、terraform の sizing モジュールでも計算して比べる"""
    cases = {
        f"{model}|{ctx}|{kv}|ollama": (model, ctx, kv, "ollama")
        for model, m in catalog.models.items()
        for ctx in CONTEXT_LENGTHS if ctx <= m["max_context"]
        for kv in KV_CACHE_TYPES
    }
    # vLLM で動かせるモデルは、f16 で vLLM の見積もりも比べる
    cases.update({
        f"{model}|{ctx}|f16|vllm": (model, ctx, "f16", "vllm")
        for model, m in catalog.models.items() if m.get("vllm")
        for ctx in CONTEXT_LENGTHS if ctx <= m["max_context"]
    })
    module = os.path.relpath(SIZING_MODULE, tmp_path)
    (tmp_path / "cases.json").write_text(json.dumps({k: list(v) for k, v in cases.items()}))
    (tmp_path / "main.tf").write_text(f"""
locals {{
  cases = jsondecode(file("${{path.module}}/cases.json"))
}}

module "sizing" {{
  source   = "{module}"
  for_each = local.cases

  models_file         = "{os.path.join(CATALOG_DIR, 'models.yaml')}"
  instance_types_file = "{os.path.join(CATALOG_DIR, 'instance_types.yaml')}"
  model               = each.value[0]
  context_length      = each.value[1]
  kv_cache_type       = each.value[2]
  engine              = each.value[3]
  vram_overhead_gib   = 1.5
}}

output "results" {{
  value = {{ for k, m in module.sizing : k => {{
    instance_type          = m.instance_type
    fitting_instance_types = m.fitting_instance_types
    vram_estimate          = m.vram_estimate
  }} }}
}}
""")
    env = {**os.environ, "TF_IN_AUTOMATION": "1", "TF_WORKSPACE": "default"}
    for args in (["init", "-input=false", "-no-color"], ["apply", "-auto-approve", "-input=false", "-no-color"]):
        subprocess.run(["terraform", *args], cwd=tmp_path, env=env, check=True, capture_output=True, text=True)
    out = subprocess.run(["terraform", "output", "-json", "results"], cwd=tmp_path, env=env, check=True, capture_output=True, text=True)
    results = json.loads(out.stdout)

    assert set(results) == set(cases)
    mismatches = []
    for key, (model, ctx, kv, engine) in cases.items():
        py = estimate(catalog, model, ctx, kv, engine=engine)
        if py["instance_type"] is None:
            # どの型にも収まらない組み合わせは、terraform では precondition で止まる(出力を比べられない)
            continue
        tf = results[key]
        if (py["instance_type"], py["fitting_instance_types"], py["vram_estimate"]) != (
            tf["instance_type"], tf["fitting_instance_types"], tf["vram_estimate"]
        ):
            mismatches.append((key, py["vram_estimate"], tf["vram_estimate"]))
    assert not mismatches, f"terraform と結果が違う: {mismatches[:3]}"


def test_vllm_uses_bf16_weights_and_90_percent_of_vram(catalog):
    r = estimate(catalog, "qwen3:8b", 8192, engine="vllm")
    e = r["vram_estimate"]
    # 16.38 GB = 15.26 GiB、KV 1.13 GiB、余裕 2.0 GiB = 18.38 GiB。g6.xlarge(22.5 GiB)x 0.9 = 20.25 GiB に収まる
    assert (e["weights_gib"], e["kv_cache_gib"], e["overhead_gib"], e["required_gib"]) == (15.26, 1.13, 2.0, 18.38)
    assert r["instance_type"] == "g6.xlarge" and e["usable_vram_gib"] == 20.25 and e["engine"] == "vllm"
    assert "g4dn.xlarge" not in r["fitting_instance_types"], "T4(15 GiB)には bf16 の 8B は収まらない"


@pytest.mark.parametrize("kwargs, key", [
    ({"model": "gemma3:12b", "engine": "vllm"}, "engine.vllm_unsupported"),
    ({"model": "qwen3:8b", "engine": "vllm", "kv_cache_type": "q8_0"}, "engine.vllm_kv_cache"),
    ({"model": "qwen3:8b", "engine": "tgi"}, "engine.invalid"),
])
def test_vllm_rejects(catalog, kwargs, key):
    with pytest.raises(SizingError) as e:
        estimate(catalog, **kwargs)
    assert e.value.key == key
