"""推論エンジンに vLLM を選んだときの API のテスト(モデルの一覧、見積もり、リージョンの料金、起動、トークン、チャット)"""

import http.server
import json
import os
import stat
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import HF_TOKEN_PARAMETER, create_app
from llm_launchpad_webui.chat import chat_once
from llm_launchpad_webui.runner import Runner
from llm_launchpad_webui.storage import VLLM, root_volume_size_gib

from test_launch import VARIABLES
from test_region_plan import FakeAwsInfo as PlanAws

HERE = os.path.dirname(os.path.abspath(__file__))


class FakeAws(PlanAws):
    def __init__(self):
        super().__init__()
        self.tokens = {}

    def hf_token_exists(self, region, name):
        return (region, name) in self.tokens

    def save_hf_token(self, region, name, token):
        self.tokens[(region, name)] = token


@pytest.fixture
def aws():
    return FakeAws()


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    return d


@pytest.fixture
def client(tmp_path, terraform_dir, aws):
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    with TestClient(create_app(Runner(str(terraform_dir), str(fake)), web_dist=None, aws_info=aws)) as c:
        yield c


VLLM_VARIABLES = {**VARIABLES, "engine": "vllm", "model_storage": "download"}


def test_models_lists_only_vllm_capable_models_with_bf16_estimates(client):
    models = client.get("/api/models", params={"engine": "vllm"}).json()["models"]
    tags = {m["model"]["tag"] for m in models}
    assert "qwen3:8b" in tags and "gemma3:12b" not in tags and "gpt-oss:20b" not in tags
    qwen = next(m for m in models if m["model"]["tag"] == "qwen3:8b")
    assert qwen["vram_estimate"]["engine"] == "vllm" and qwen["instance_type"] == "g6.xlarge"
    assert qwen["model"]["vllm"]["hf_repo"] == "Qwen/Qwen3-8B"


def test_models_rejects_quantized_kv_cache_for_vllm(client):
    res = client.get("/api/models", params={"engine": "vllm", "kv_cache_type": "q8_0"}, headers={"Accept-Language": "en"})
    assert res.status_code == 422 and "f16" in res.json()["detail"]


def test_region_plan_offers_only_download_with_the_bigger_root_volume(client):
    p = client.get("/api/region-plan", params={"model": "qwen3:8b", "engine": "vllm"}).json()
    assert p["instance_type"] == "g6.xlarge"
    assert [o["id"] for o in p["storage_options"]] == ["download"]
    assert p["storage_options"][0]["root_volume_gib"] == root_volume_size_gib(16.4, True, VLLM["root_volume_extra_gib"]) == 148


def test_hf_token_is_saved_without_being_returned(client, aws):
    assert client.get("/api/hf-token").json() == {"parameter": HF_TOKEN_PARAMETER, "exists": False}
    assert client.post("/api/hf-token", json={"token": "not-a-token"}).status_code == 422
    token = "hf_" + "a" * 34
    res = client.post("/api/hf-token", json={"token": token, "region": "ap-northeast-1"})
    assert res.status_code == 200 and token not in res.text
    assert aws.tokens == {("ap-northeast-1", HF_TOKEN_PARAMETER): token}
    assert client.get("/api/hf-token").json()["exists"] is True


def test_launch_gated_model_needs_the_token_parameter(client, terraform_dir):
    res = client.post("/api/launch", json={"workspace": "llama", "variables": {**VLLM_VARIABLES, "model": "llama3.1:8b"}},
                      headers={"Accept-Language": "en"})
    assert res.status_code == 422 and "gated" in res.json()["detail"]
    ok = client.post("/api/launch", json={"workspace": "llama", "variables": {
        **VLLM_VARIABLES, "model": "llama3.1:8b", "hf_token_parameter_name": HF_TOKEN_PARAMETER}})
    assert ok.status_code == 201, ok.text
    written = json.loads((terraform_dir / "tfvars" / "llama.tfvars.json").read_text())
    assert written["engine"] == "vllm" and written["hf_token_parameter_name"] == HF_TOKEN_PARAMETER
    assert "hf_" not in json.dumps(written).replace("hf_token_parameter_name", ""), "トークンそのものは tfvars に書かない"


def test_launch_rejects_models_vllm_cannot_run(client):
    res = client.post("/api/launch", json={"workspace": "gemma", "variables": {**VLLM_VARIABLES, "model": "gemma3:12b"}})
    assert res.status_code == 422


class FakeVllmChat(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeVllmChat.seen = (self.path, body)
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": "こんにちは"}}], "usage": {"completion_tokens": 5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


def test_chat_uses_the_openai_api_for_vllm():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeVllmChat)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        reply = chat_once(f"http://127.0.0.1:{server.server_port}", "qwen3:8b", [{"role": "user", "content": "hi"}], engine="vllm")
    finally:
        server.shutdown()
    assert FakeVllmChat.seen[0] == "/v1/chat/completions" and FakeVllmChat.seen[1]["stream"] is False
    assert reply["message"] == {"role": "assistant", "content": "こんにちは"} and reply["output_tokens"] == 5
