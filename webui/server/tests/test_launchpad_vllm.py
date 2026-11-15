"""インスタンスで動かす launchpad.py の、推論エンジンが vLLM のときのテスト。vLLM は偽物の HTTP サーバーで代える"""

import http.server
import importlib.util
import json
import os
import subprocess
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHPAD = os.path.join(HERE, "..", "..", "..", "terraform", "modules", "bootstrap", "files", "launchpad.py")


class FakeVllm(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
        elif self.path == "/version":
            self._json({"version": "0.10.1"})
        elif self.path == "/v1/models":
            self._json({"data": [{"id": "qwen3:8b", "max_model_len": 8192}, {"id": "Qwen/Qwen3-8B", "max_model_len": 8192}]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeVllm.requests.append((self.path, body))
        if self.path == "/v1/completions":
            self._json({"choices": [{"text": " OK"}]})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        events = [
            {"choices": [{"delta": {"reasoning_content": "Let me"}}]},
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 11}},
        ]
        for e in events:
            self.wfile.write(f"data: {json.dumps(e)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.02)
        self.wfile.write(b"data: [DONE]\n\n")

    def _json(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def launchpad(monkeypatch, tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeVllm)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeVllm.requests = []
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("LAUNCHPAD_STATE_FILE", str(tmp_path / "status.json"))
    monkeypatch.delenv("LAUNCHPAD_ENGINE", raising=False)
    (tmp_path / "engine").write_text("vllm\n")
    monkeypatch.setenv("LAUNCHPAD_ENGINE_FILE", str(tmp_path / "engine"))
    spec = importlib.util.spec_from_file_location("launchpad", LAUNCHPAD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "gpu_memory", lambda: [{"index": 0, "name": "NVIDIA L4", "memory_used_mib": 20000, "memory_total_mib": 23034}])
    yield module
    server.shutdown()


def test_engine_is_read_from_the_file_when_the_environment_has_none(launchpad, monkeypatch, tmp_path):
    assert launchpad.engine() == "vllm", "SSM の send-command で動かすときは、起動処理が書いたファイルを読む"
    monkeypatch.setenv("LAUNCHPAD_ENGINE", "ollama")
    assert launchpad.engine() == "ollama"
    monkeypatch.delenv("LAUNCHPAD_ENGINE")
    monkeypatch.setattr(launchpad, "ENGINE_FILE", str(tmp_path / "missing"))
    assert launchpad.engine() == "ollama", "ファイルがなければ Ollama"


def test_wait_server_and_verify_use_the_openai_api(launchpad):
    launchpad.wait_server(timeout_sec=5)
    assert launchpad.load_state()["vllm_version"] == "0.10.1"
    launchpad.verify("qwen3:8b")
    path, body = FakeVllm.requests[-1]
    assert path == "/v1/completions" and body["model"] == "qwen3:8b" and body["max_tokens"] == 8
    assert launchpad.load_state()["verify"]["loaded"] == {"context_length": 8192}


def test_bench_measures_ttft_and_decode_rate(launchpad, capsys):
    out = launchpad.bench("qwen3:8b", runs=1, num_predict=64)
    assert len(FakeVllm.requests) == 1 + len(launchpad.BENCH_PROMPTS), "最初の1回は数えない"
    path, body = FakeVllm.requests[1]
    assert path == "/v1/chat/completions"
    assert body["max_tokens"] == 64 and body["temperature"] == 0 and body["seed"] == 0 and body["stream_options"] == {"include_usage": True}
    assert out["engine"] == "vllm" and out["thinking"] is True
    assert out["ttft_sec"] <= out["first_response_sec"], "TTFT は考えている間のトークンも数える"
    # 最初のトークンから最後のトークンまでに 10 トークン(11 - 1)
    assert out["output_tokens_per_sec"] > 0 and out["prompt_tokens_per_sec"] is None
    assert out["results"][0]["output_tokens"] == 11 and out["loaded"] == {"context_length": 8192}
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["engine"] == "vllm"


def test_wait_server_fails_when_the_container_stopped(launchpad, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")
    spec = importlib.util.spec_from_file_location("launchpad2", LAUNCHPAD)
    lp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lp)

    def fake_run(cmd, **kwargs):
        out = "exited" if cmd[:2] == ["docker", "inspect"] else "torch.OutOfMemoryError: CUDA out of memory"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(lp.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        lp.wait_server(timeout_sec=60)


def test_track_download_writes_progress_until_the_stage_changes(launchpad, tmp_path):
    cache = tmp_path / "hf"
    (cache / "models--Qwen--Qwen3-8B" / "blobs").mkdir(parents=True)
    (cache / "models--Qwen--Qwen3-8B" / "blobs" / "a").write_bytes(b"x" * 250)
    launchpad.set_status("pulling_model")
    t = threading.Thread(target=launchpad.track_download, args=(str(cache), 1000, 0.05), daemon=True)
    t.start()
    time.sleep(0.2)
    pull = launchpad.load_state()["pull"]
    assert pull["percent"] == 25.0 and pull["completed_bytes"] == 250 and pull["total_bytes"] == 1000
    launchpad.set_status("verifying")
    t.join(timeout=2)
    assert not t.is_alive(), "次の段階に進んだら止まる"
    assert "pull" not in launchpad.load_state(), "止まったあとに取得の進み具合を書き戻さない"


def test_track_download_never_reports_100_percent_before_the_download_ends(launchpad, tmp_path):
    (tmp_path / "hf").mkdir()
    (tmp_path / "hf" / "w").write_bytes(b"x" * 2000)
    launchpad.set_status("pulling_model")
    t = threading.Thread(target=launchpad.track_download, args=(str(tmp_path / "hf"), 1000, 0.05), daemon=True)
    t.start()
    time.sleep(0.15)
    assert launchpad.load_state()["pull"]["percent"] == 99.0, "重みの大きさは見積もりなので、超えても 100% にしない"
    launchpad.set_status("failed_stage", "failed")
    t.join(timeout=2)
    assert not t.is_alive() and launchpad.load_state()["state"] == "failed", "失敗を上書きしない"


def test_idle_check_counts_vllm_requests_from_the_container_log(launchpad, monkeypatch):
    log = "\n".join([
        '2026-11-15T06:00:00.123456789Z INFO:     127.0.0.1:50000 - "POST /v1/chat/completions HTTP/1.1" 200 OK',
        '2026-11-15T06:01:00.000000001Z INFO:     127.0.0.1:50001 - "GET /health HTTP/1.1" 200 OK',
        '2026-11-15T06:02:00.5Z INFO:     127.0.0.1:50002 - "GET /v1/models HTTP/1.1" 200 OK',
        '2026-11-15T06:03:00Z INFO 11-15 06:03:00 [loggers.py:123] Engine 000: Avg prompt throughput: 0.0 tokens/s',
        '2026-11-15T06:04:00.000Z INFO:     127.0.0.1:50003 - "POST /v1/completions HTTP/1.1" 200 OK',
    ])
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=log, stderr="")

    monkeypatch.setattr(launchpad.subprocess, "run", fake_run)
    times = launchpad.vllm_request_times(30)
    assert calls[0] == ["docker", "logs", "--since", "30m", "--timestamps", "vllm"]
    assert times == [1794722400.0, 1794722640.0], "死活の確認やモデルの一覧は数えない"
