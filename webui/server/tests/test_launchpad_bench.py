"""インスタンスで動かすベンチマーク(terraform/modules/bootstrap/files/launchpad.py の bench)のテスト。
Ollama は、決まった応答を返す偽物の HTTP サーバーで代える"""

import http.server
import importlib.util
import json
import os
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHPAD = os.path.join(HERE, "..", "..", "..", "terraform", "modules", "bootstrap", "files", "launchpad.py")


class FakeOllama(http.server.BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/api/ps":
            self._json({"models": [{"name": "qwen3:8b", "size": 6_000_000_000, "size_vram": 5_500_000_000, "context_length": 8192}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(body)
        self.send_response(200)
        self.end_headers()
        chunks = [{"response": "Hel", "done": False}, {"response": "lo", "done": False}, {
            "response": "", "done": True,
            "eval_count": 100, "eval_duration": 2_000_000_000,
            "prompt_eval_count": 20, "prompt_eval_duration": 100_000_000,
            "load_duration": 3_000_000_000 if len(FakeOllama.requests) == 1 else 0,
        }]
        for c in chunks:
            self.wfile.write((json.dumps(c) + "\n").encode())

    def _json(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def launchpad(monkeypatch, tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeOllama.requests = []
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("LAUNCHPAD_STATE_FILE", str(tmp_path / "status.json"))
    spec = importlib.util.spec_from_file_location("launchpad", LAUNCHPAD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "gpu_memory", lambda: [{"index": 0, "name": "NVIDIA L4", "memory_used_mib": 6000, "memory_total_mib": 23034}])
    yield module
    server.shutdown()


def test_bench_measures_and_prints_json(launchpad, capsys):
    out = launchpad.bench("qwen3:8b", runs=2, num_predict=64)
    assert len(FakeOllama.requests) == 1 + 2 * len(launchpad.BENCH_PROMPTS), "最初の1回はモデルを載せるためで、数えない"
    assert FakeOllama.requests[1]["options"] == {"num_predict": 64, "temperature": 0, "seed": 0}
    assert out["output_tokens_per_sec"] == 50.0 and out["prompt_tokens_per_sec"] == 200.0
    assert out["warmup_load_sec"] == 3.0 and out["ttft_sec"] is not None
    assert out["loaded"]["size_vram_bytes"] == 5_500_000_000 and out["gpus"][0]["memory_used_mib"] == 6000
    assert {r["prompt_id"] for r in out["results"]} == {p["id"] for p in launchpad.BENCH_PROMPTS}
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert json.loads(last) == out, "WebUI は標準出力の最後の行を読む"


def test_bench_command(launchpad, capsys):
    assert launchpad.main(["bench", "qwen3:8b", "--num-predict", "16"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["runs"] == 1
