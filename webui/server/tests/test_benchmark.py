"""ベンチマークとチャットの API のテスト。インスタンスでの計測(SSM の send-command)と Ollama は偽物を使う"""

import csv
import http.server
import io
import json
import os
import stat
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.benchmark import CSV_COLUMNS, bench_command, parse_output, usd_per_million_tokens
from llm_launchpad_webui.runner import Runner

from test_environments import FakeAwsInfo, instance, state_with_instance, write_state, write_tfvars
from test_launch import VARIABLES

HERE = os.path.dirname(os.path.abspath(__file__))

RAW = {
    "model": "qwen3:8b", "measured_at": "2026-10-28T03:00:00Z", "num_predict": 256, "runs": 1, "warmup_load_sec": 4.2,
    "ttft_sec": 0.12, "output_tokens_per_sec": 40.0, "prompt_tokens_per_sec": 900.0,
    "results": [{"prompt_id": "short", "run": 1, "ttft_sec": 0.12, "output_tokens_per_sec": 40.0}],
    "loaded": {"size_vram_bytes": 6_000_000_000, "context_length": 8192},
    "gpus": [{"index": 0, "name": "NVIDIA L4", "memory_used_mib": 7168, "memory_total_mib": 23034}],
}


class FakeBenchAws(FakeAwsInfo):
    def __init__(self):
        super().__init__()
        self.commands = []
        self.command_result = {"status": "Success", "exit_code": 0, "stdout": "log line\n" + json.dumps(RAW) + "\n", "stderr": ""}
        self.command_error = None

    def run_command(self, region, instance_id, command, timeout_sec=3600, poll_sec=3):
        self.commands.append((region, instance_id, command))
        if self.command_error:
            raise self.command_error
        return {"command_id": "c-1", **self.command_result}


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    (d / "fake-state").mkdir(parents=True)
    write_state(d, "qwen3-8b", state_with_instance())
    write_tfvars(d, "qwen3-8b")
    return d


@pytest.fixture
def aws():
    a = FakeBenchAws()
    a.instances = [instance()]
    return a


@pytest.fixture
def results_file(tmp_path):
    return tmp_path / "benchmarks" / "results.jsonl"


@pytest.fixture
def client(tmp_path, terraform_dir, aws, results_file):
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    app = create_app(Runner(str(terraform_dir), str(fake)), web_dist=None, aws_info=aws, benchmark_file=str(results_file))
    with TestClient(app) as c:
        yield c


def wait_job(client, job_id, headers=None):
    for _ in range(100):
        job = client.get(f"/api/benchmarks/jobs/{job_id}", headers=headers or {}).json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("計測が終わりませんでした")


def test_parse_output_reads_last_json_line():
    assert parse_output("starting\n" + json.dumps({"a": 1}) + "\n\n") == {"a": 1}
    with pytest.raises(Exception):
        parse_output("usage: launchpad.py ...\n")


def test_usd_per_million_tokens():
    # 1時間 0.36 ドルで 40 tok/s → 100万トークンに 6944 秒 → 0.6944 時間
    assert usd_per_million_tokens(0.36, 40.0) == 2.5
    assert usd_per_million_tokens(None, 40.0) is None and usd_per_million_tokens(0.36, 0) is None


def test_bench_command_quotes_model():
    assert bench_command("qwen3:8b", 3) == "python3 /opt/llm-launchpad/launchpad.py bench qwen3:8b --runs 3"
    assert bench_command("a; rm -rf /", 1).startswith("python3 /opt/llm-launchpad/launchpad.py bench 'a; rm -rf /'")


def test_benchmark_saves_result_with_cost_and_vram(client, aws, results_file):
    job = client.post("/api/environments/qwen3-8b/benchmark", json={"runs": 1}).json()
    assert job["status"] == "running" and job["model"] == "qwen3:8b"
    done = wait_job(client, job["id"])
    assert done["status"] == "succeeded", done
    assert aws.commands == [("ap-northeast-1", "i-0aaa", bench_command("qwen3:8b", 1))]

    r = done["result"]
    assert r["instance_type"] == "g6.xlarge" and r["gpu"] == "NVIDIA L4" and r["purchase_option"] == "spot"
    assert r["hourly_usd"] == 0.36 and r["usd_per_million_tokens"] == 2.5, "スポットは、その AZ のスポット価格"
    assert r["vram_measured_gib"] == 7.0 and r["vram_estimate_gib"] is not None
    assert r["vram_diff_gib"] == round(7.0 - r["vram_estimate_gib"], 2)
    assert r["context_length"] == 8192 and r["kv_cache_type"] == "f16"

    history = client.get("/api/benchmarks").json()["benchmarks"]
    assert [h["id"] for h in history] == [r["id"]] and history[0]["results"] == RAW["results"]
    assert results_file.exists()

    res = client.get("/api/benchmarks.csv")
    assert res.headers["content-type"].startswith("text/csv") and "attachment" in res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert list(rows[0]) == CSV_COLUMNS and rows[0]["usd_per_million_tokens"] == "2.5"


def test_benchmark_history_newest_first(client):
    ids = [wait_job(client, client.post("/api/environments/qwen3-8b/benchmark", json={}).json()["id"])["result"]["id"] for _ in range(2)]
    assert [h["id"] for h in client.get("/api/benchmarks").json()["benchmarks"]] == ids[::-1]


def test_benchmark_failure_is_reported_in_the_language(client, aws):
    aws.command_result = {"status": "Failed", "exit_code": 2, "stdout": "", "stderr": "usage: launchpad.py ... invalid choice: 'bench'"}
    job = client.post("/api/environments/qwen3-8b/benchmark", json={}).json()
    done = wait_job(client, job["id"], headers={"Accept-Language": "en"})
    assert done["status"] == "failed" and done["error"].startswith("The benchmark failed (Failed, exit code 2)")
    assert client.get("/api/benchmarks").json()["benchmarks"] == []


def test_benchmark_aws_error(client, aws):
    aws.command_error = AwsInfoError("aws.command_failed", instance_id="i-0aaa", error="AccessDenied")
    done = wait_job(client, client.post("/api/environments/qwen3-8b/benchmark", json={}).json()["id"])
    assert done["status"] == "failed" and "AccessDenied" in done["error"]


def test_benchmark_needs_running_instance(client, aws):
    aws.instances = [instance(state="stopped")]
    res = client.post("/api/environments/qwen3-8b/benchmark", json={})
    assert res.status_code == 409 and "stopped" in res.json()["detail"]
    assert client.post("/api/environments/qwen3-8b/benchmark", json={"runs": 0}).status_code == 422
    assert client.post("/api/environments/nothing/benchmark", json={}).status_code == 404
    assert client.get("/api/benchmarks/jobs/none").status_code == 404


class FakeOllamaChat(http.server.BaseHTTPRequestHandler):
    received = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        FakeOllamaChat.received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        data = json.dumps({"message": {"role": "assistant", "content": "Hi!"}, "eval_count": 20, "eval_duration": 500_000_000}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)


def test_chat_sends_to_ollama_endpoint(client, monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaChat)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        client.app.state.chat_outputs["qwen3-8b"] = {"ollama_endpoint": endpoint}
        res = client.post("/api/environments/qwen3-8b/chat", json={"messages": [{"role": "user", "content": "Hello"}]}).json()
        assert res["message"] == {"role": "assistant", "content": "Hi!"} and res["output_tokens_per_sec"] == 40.0
        assert FakeOllamaChat.received[-1] == {"model": "qwen3:8b", "messages": [{"role": "user", "content": "Hello"}], "stream": False}
    finally:
        server.shutdown()


def test_chat_unreachable_tells_port_forward(client):
    client.app.state.chat_outputs["qwen3-8b"] = {"ollama_endpoint": "http://127.0.0.1:9", "ssm_port_forward_command": "aws ssm start-session ..."}
    res = client.post("/api/environments/qwen3-8b/chat", json={"messages": [{"role": "user", "content": "Hello"}]}, headers={"Accept-Language": "en"})
    assert res.status_code == 502 and "port forward" in res.json()["detail"] and "aws ssm start-session" in res.json()["detail"]


def test_chat_reads_endpoint_from_terraform_output(client):
    res = client.post("/api/environments/qwen3-8b/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
    assert res.status_code == 502, "fake_terraform の output の送り先には何もいない"
    assert client.app.state.chat_outputs["qwen3-8b"]["ollama_endpoint"] == "http://127.0.0.1:9"
    assert client.post("/api/environments/nothing/chat", json={"messages": []}).status_code == 404
