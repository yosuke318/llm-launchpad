"""モデル一覧と見積もりの API のテスト。リポジトリの catalog/ を読む"""

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.runner import Runner


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Runner(str(tmp_path)), web_dist=None)) as c:
        yield c


def test_catalog_lists_vendors_and_options(client):
    c = client.get("/api/catalog").json()
    assert c["vendors"][:2] == ["Meta", "Google"], "提供元はモデル定義に出てくる順"
    assert {"Alibaba", "OpenAI", "NVIDIA", "Mistral"} <= set(c["vendors"])
    assert [e["id"] for e in c["engines"] if e["available"]] == ["ollama"], "初期版のエンジンは Ollama だけ"
    assert c["defaults"] == {"context_length": 8192, "kv_cache_type": "f16"}
    assert c["instance_types"][0]["type"] == "g4dn.xlarge"


def test_models_filtered_by_vendor_with_estimates(client):
    models = client.get("/api/models", params={"vendor": "Alibaba"}).json()["models"]
    assert models and all(m["model"]["vendor"] == "Alibaba" for m in models)
    qwen = next(m for m in models if m["model"]["tag"] == "qwen3:8b")
    assert qwen["instance_type"] == "g4dn.xlarge"
    assert qwen["vram_estimate"]["required_gib"] == 7.47
    assert qwen["context_capped"] is False


def test_models_cap_context_at_model_max(client):
    models = client.get("/api/models", params={"vendor": "Alibaba", "context_length": 131072}).json()["models"]
    qwen = next(m for m in models if m["model"]["tag"] == "qwen3:8b")
    assert qwen["context_capped"] is True, "qwen3:8b の最大は 40960"
    assert qwen["vram_estimate"]["context_length"] == 40960


def test_context_length_changes_instance_type(client):
    small = client.get("/api/estimate", params={"model": "llama3.3:70b", "context_length": 8192}).json()
    large = client.get("/api/estimate", params={"model": "llama3.3:70b", "context_length": 32768}).json()
    assert (small["instance_type"], large["instance_type"]) == ("g6e.xlarge", "g6.12xlarge")


@pytest.mark.parametrize("params", [
    {"model": "no-such:1b"},
    {"model": "qwen3:8b", "context_length": 65536},
    {"model": "qwen3:8b", "kv_cache_type": "q2"},
])
def test_estimate_rejects_invalid_input(client, params):
    assert client.get("/api/estimate", params=params).status_code == 422


def test_models_rejects_invalid_kv_cache_type(client):
    assert client.get("/api/models", params={"kv_cache_type": "q2"}).status_code == 422
