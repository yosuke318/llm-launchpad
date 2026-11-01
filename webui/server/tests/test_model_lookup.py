"""一覧にないモデルを調べて追加する処理のテスト。registry には問い合わせず、偽の応答を返す"""

import io
import json
import os
import stat
import struct
import sys
import urllib.error

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.model_lookup import ModelLookupError, lookup, parse_ref, read_gguf_metadata
from llm_launchpad_webui.runner import Runner

from test_app import events

HERE = os.path.dirname(os.path.abspath(__file__))


def gguf_string(s):
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def gguf(metadata):
    """GGUF v3 の先頭(メタデータまで)。値は int なら u32、str なら文字列、list なら u32 か文字列の配列"""
    out = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(metadata))
    for key, value in metadata.items():
        out += gguf_string(key)
        if isinstance(value, str):
            out += struct.pack("<I", 8) + gguf_string(value)
        elif isinstance(value, list) and value and isinstance(value[0], str):
            out += struct.pack("<I", 9) + struct.pack("<I", 8) + struct.pack("<Q", len(value)) + b"".join(gguf_string(v) for v in value)
        elif isinstance(value, list):
            out += struct.pack("<I", 9) + struct.pack("<I", 4) + struct.pack("<Q", len(value)) + struct.pack(f"<{len(value)}I", *value)
        else:
            out += struct.pack("<I", 4) + struct.pack("<I", value)
    return out


METADATA = {
    "general.architecture": "llama",
    "general.name": "Llama 3.2 1B Instruct",
    "general.size_label": "1B",
    "general.license": "llama3.2",
    "general.file_type": 15,
    "llama.block_count": 16,
    "llama.context_length": 131072,
    "llama.embedding_length": 2048,
    "llama.attention.head_count": 32,
    "llama.attention.head_count_kv": 8,
    "tokenizer.ggml.tokens": ["a", "b"],
}


def fake_registry(metadata=METADATA, config=None, status=None):
    """registry の偽物。manifest、config、モデルの層の順に呼ばれる。開いた URL と送ったヘッダーを calls に残す"""
    calls = []
    manifest = {
        "config": {"digest": "sha256:config"},
        "layers": [
            {"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:model", "size": 807694464},
            {"mediaType": "application/vnd.ollama.image.projector", "digest": "sha256:proj", "size": 100_000_000},
            {"mediaType": "application/vnd.ollama.image.template", "digest": "sha256:tmpl", "size": 1481},
        ],
    }

    def opener(url, headers=None):
        calls.append((url, headers or {}))
        if status:
            raise urllib.error.HTTPError(url, status, "error", {}, None)
        if "/manifests/" in url:
            return io.BytesIO(json.dumps(manifest).encode())
        if url.endswith("sha256:config"):
            return io.BytesIO(json.dumps(config or {"model_type": "1.24B", "file_type": "unknown"}).encode())
        return io.BytesIO(gguf(metadata) + b"\x00" * 64)

    opener.calls = calls
    return opener


@pytest.mark.parametrize("text, expected", [
    ("qwen3:8b", ("qwen3:8b", "https://registry.ollama.ai/v2/library/qwen3", "8b", "Ollama")),
    ("  Qwen3  ", ("qwen3:latest", "https://registry.ollama.ai/v2/library/qwen3", "latest", "Ollama")),
    ("someone/model:q4", ("someone/model:q4", "https://registry.ollama.ai/v2/someone/model", "q4", "Ollama")),
    ("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M",
     ("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", "https://hf.co/v2/bartowski/Llama-3.2-1B-Instruct-GGUF", "Q4_K_M", "Hugging Face")),
    ("https://huggingface.co/unsloth/Qwen3-4B-GGUF/",
     ("hf.co/unsloth/Qwen3-4B-GGUF:latest", "https://hf.co/v2/unsloth/Qwen3-4B-GGUF", "latest", "Hugging Face")),
])
def test_parse_ref(text, expected):
    assert parse_ref(text) == expected


@pytest.mark.parametrize("text", ["", "a b", "hf.co/only-user", "../etc/passwd", "a/b/c:d", "qwen3:8b; rm -rf /"])
def test_parse_ref_rejects_other_input(text):
    with pytest.raises(ModelLookupError):
        parse_ref(text)


def test_lookup_reads_definition_from_gguf_metadata():
    opener = fake_registry()
    tag, definition = lookup("hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M", opener)
    assert tag == "hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M"
    assert definition == {
        "vendor": "Hugging Face",
        "name": "Llama 3.2 1B Instruct",
        "params_b": 1.24,
        # config に量子化がなければ GGUF の general.file_type から
        "quantization": "Q4_K_M",
        # 重みと projector の合計。テンプレートは入れない
        "file_size_gb": 0.91,
        "max_context": 131072,
        "layers": 16,
        "kv_heads": 8,
        # key_length がなければ embedding_length / head_count
        "head_dim": 64,
        "license": "llama3.2",
    }
    assert opener.calls[0][1]["Accept"] == "application/vnd.docker.distribution.manifest.v2+json"
    assert opener.calls[2][1]["Range"].startswith("bytes=0-"), "重み全体は取らない"


def test_lookup_counts_attention_layers_of_hybrid_model():
    metadata = {
        "general.architecture": "granitehybrid",
        "granitehybrid.block_count": 4,
        "granitehybrid.context_length": 1048576,
        "granitehybrid.attention.head_count": 12,
        "granitehybrid.attention.head_count_kv": [0, 4, 0, 4],
        "granitehybrid.attention.key_length": 128,
    }
    _, definition = lookup("granite4:tiny-h", fake_registry(metadata, {"model_type": "6.9B", "file_type": "Q4_K_M"}))
    assert (definition["layers"], definition["attention_layers"], definition["kv_heads"]) == (4, 2, 4)
    assert definition["params_b"] == 6.9 and definition["quantization"] == "Q4_K_M"
    assert definition["name"] == "granite4:tiny-h", "general.name がなければタグ"


def test_lookup_rejects_unreadable_architecture():
    metadata = {"general.architecture": "clip", "clip.block_count": 4}
    with pytest.raises(ModelLookupError, match="clip"):
        lookup("someone/vision:latest", fake_registry(metadata))


@pytest.mark.parametrize("status", [401, 404])
def test_lookup_reports_missing_model(status):
    with pytest.raises(ModelLookupError, match="見つかりません"):
        lookup("nosuch:latest", fake_registry(status=status))


def test_read_gguf_metadata_stops_at_tokenizer():
    data = gguf(METADATA)
    # トークナイザーの語彙の前で切っても、そこまでで読み終える
    cut = data.index(b"tokenizer.ggml.tokens") + len("tokenizer.ggml.tokens")
    metadata = read_gguf_metadata(io.BytesIO(data[:cut]))
    assert metadata["llama.block_count"] == 16
    assert "tokenizer.ggml.tokens" not in metadata


def test_read_gguf_metadata_rejects_other_files():
    with pytest.raises(ValueError):
        read_gguf_metadata(io.BytesIO(b"PK\x03\x04" + b"\x00" * 32))


ADDED_TAG = "hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M"


@pytest.fixture
def client(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    app = create_app(Runner(str(d), str(fake)), web_dist=None, model_lookup=lambda ref: lookup(ref, fake_registry()))
    with TestClient(app) as c:
        c.terraform_dir = d
        yield c


def test_added_model_is_listed_and_estimated(client):
    res = client.post("/api/models/added", json={"ref": "huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M"})
    assert res.status_code == 201, res.text
    assert res.json()["tag"] == ADDED_TAG
    assert "Hugging Face" in client.get("/api/catalog").json()["vendors"]

    models = {m["model"]["tag"]: m for m in client.get("/api/models", params={"vendor": "Hugging Face"}).json()["models"]}
    assert list(models) == [ADDED_TAG]
    m = models[ADDED_TAG]
    assert m["model"]["added"] is True
    assert m["instance_type"] == "g4dn.xlarge"
    assert m["vram_estimate"]["weights_gib"] == 0.85
    assert client.get("/api/estimate", params={"model": ADDED_TAG}).status_code == 200


def test_launch_of_added_model_writes_definition_to_tfvars(client):
    client.post("/api/models/added", json={"ref": ADDED_TAG})
    variables = {"model": ADDED_TAG, "region": "ap-northeast-1", "model_definition": {"layers": 1}}
    run = client.post("/api/launch", json={"workspace": "llama-1b", "variables": variables})
    assert run.status_code == 201, run.text
    events(client, run.json()["id"])
    tfvars = json.loads((client.terraform_dir / "tfvars" / "llama-1b.tfvars.json").read_text())
    assert tfvars["model_definition"]["layers"] == 16, "画面から送られた定義ではなく、追加したときの定義を書く"
    assert tfvars["model_definition"]["file_size_gb"] == 0.91
    assert "added" not in tfvars["model_definition"]


def test_launch_of_catalog_model_has_no_definition(client):
    run = client.post("/api/launch", json={"workspace": "qwen3-8b", "variables": {"model": "qwen3:8b", "model_definition": {"layers": 1}}})
    events(client, run.json()["id"])
    assert "model_definition" not in json.loads((client.terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").read_text())


def test_model_in_catalog_is_not_added(client):
    assert client.post("/api/models/added", json={"ref": "qwen3:8b"}).status_code == 409


def test_invalid_ref_is_rejected(client):
    assert client.post("/api/models/added", json={"ref": "not a model"}).status_code == 422


def test_added_model_can_be_removed(client):
    client.post("/api/models/added", json={"ref": ADDED_TAG})
    assert client.delete("/api/models/added", params={"tag": ADDED_TAG}).status_code == 200
    assert ADDED_TAG not in [m["model"]["tag"] for m in client.get("/api/models").json()["models"]]
    assert client.delete("/api/models/added", params={"tag": ADDED_TAG}).status_code == 404
