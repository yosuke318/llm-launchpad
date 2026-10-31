"""閉じたネットワーク用の処理(terraform/modules/bootstrap/files/launchpad.py の check-local と restore-s3 --offline)のテスト。
Ollama は、決まった応答を返す偽物の HTTP サーバーで代える"""

import http.server
import importlib.util
import json
import os
import subprocess
import threading

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHPAD = os.path.join(HERE, "..", "..", "..", "terraform", "modules", "bootstrap", "files", "launchpad.py")


class FakeOllama(http.server.BaseHTTPRequestHandler):
    models = set()
    paths = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        FakeOllama.paths.append(self.path)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/show" and body["model"] in FakeOllama.models:
            self._json(200, {"modelfile": ""})
        else:
            self._json(404, {"error": f"model '{body['model']}' not found"})

    def _json(self, code, value):
        data = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def launchpad(monkeypatch, tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeOllama.models = {"qwen3:8b"}
    FakeOllama.paths = []
    monkeypatch.setenv("OLLAMA_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("LAUNCHPAD_STATE_FILE", str(tmp_path / "status.json"))
    spec = importlib.util.spec_from_file_location("launchpad", LAUNCHPAD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    server.shutdown()


def test_check_local_passes_when_model_is_present(launchpad):
    launchpad.check_local("qwen3:8b")
    assert FakeOllama.paths == ["/api/show"], "Ollama library に問い合わせる /api/pull は使わない"
    assert launchpad.load_state()["pull"]["percent"] == 100.0


def test_check_local_fails_when_model_is_missing(launchpad):
    with pytest.raises(RuntimeError, match="gpt-oss:20b が手元にありません"):
        launchpad.check_local("gpt-oss:20b")


def test_check_local_command_marks_failed(launchpad):
    assert launchpad.main(["check-local", "gpt-oss:20b"]) == 1
    assert launchpad.load_state()["state"] == "failed"


def missing_in_s3(*args, check=True):
    return subprocess.CompletedProcess(["aws", *args], 1, "", "Not Found")


def test_restore_s3_offline_fails_when_model_is_missing(launchpad, monkeypatch, tmp_path):
    monkeypatch.setattr(launchpad, "aws", missing_in_s3)
    with pytest.raises(RuntimeError, match="s3://models/ollama/models/manifests/registry.ollama.ai/library/qwen3/8b がありません"):
        launchpad.restore_s3("qwen3:8b", "models", str(tmp_path), offline=True)


def test_restore_s3_online_falls_back_to_pull(launchpad, monkeypatch, tmp_path):
    monkeypatch.setattr(launchpad, "aws", missing_in_s3)
    launchpad.restore_s3("qwen3:8b", "models", str(tmp_path))
    assert launchpad.load_state()["cache"]["hit"] is False
