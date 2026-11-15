"""OpenAI 互換 API の中継のテスト。Ollama は手元で動かす偽物(/v1 の応答とストリーミング)を使う"""

import http.server
import json
import threading

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui import openai_relay
from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.runner import Runner

from test_environments import write_tfvars

TOKEN = "correct-horse-battery-staple"
WS = "qwen3-8b"


class FakeOllama(http.server.BaseHTTPRequestHandler):
    """受け取ったものを seen に残す。stream なら SSE を3つに分けて送る"""
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        FakeOllama.seen.append(("GET", self.path, dict(self.headers), None))
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "qwen3:8b", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.seen.append(("POST", self.path, dict(self.headers), body))
        if body.get("model") == "missing":
            self._json(404, {"error": {"message": "model \"missing\" not found", "type": "api_error"}})
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for word in ["こん", "にち", "は"]:
                chunk = {"choices": [{"delta": {"content": word}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json(200, {"choices": [{"message": {"role": "assistant", "content": "こんにちは"}}], "model": body["model"]})

    def _json(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def ollama():
    FakeOllama.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def make(tmp_path, endpoint, client=("192.168.1.20", 50000), token=TOKEN):
    d = tmp_path / "terraform"
    d.mkdir(exist_ok=True)
    write_tfvars(d, WS)
    app = create_app(Runner(str(d)), web_dist=None, access_token=token, login_failure_delay_sec=0)
    app.state.chat_outputs[(WS, None)] = {"ollama_endpoint": endpoint, "ssm_port_forward_command": "aws ssm start-session ..."}
    return TestClient(app, client=client)


AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = f"/api/environments/{WS}/openai/v1"


def test_chat_completions_are_relayed_without_the_passphrase(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        res = c.post(f"{BASE}/chat/completions", headers=AUTH, json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "hi"}]})
        assert res.status_code == 200, res.text
        assert res.json()["choices"][0]["message"]["content"] == "こんにちは"
    method, path, headers, body = FakeOllama.seen[-1]
    assert (method, path) == ("POST", "/v1/chat/completions") and body["messages"][0]["content"] == "hi"
    assert not any(k.lower() == "authorization" for k in headers), "合言葉を Ollama に渡さない"


def test_streaming_is_passed_through(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        with c.stream("POST", f"{BASE}/chat/completions", headers=AUTH,
                      json={"model": "qwen3:8b", "stream": True, "messages": [{"role": "user", "content": "hi"}]}) as res:
            assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
            text = "".join(res.iter_text())
    words = [json.loads(line[6:])["choices"][0]["delta"]["content"] for line in text.splitlines() if line.startswith("data: {")]
    assert words == ["こん", "にち", "は"] and text.rstrip().endswith("data: [DONE]")


def test_models_list(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        assert c.get(f"{BASE}/models", headers=AUTH).json()["data"][0]["id"] == "qwen3:8b"


def test_ollama_errors_are_passed_through(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        res = c.post(f"{BASE}/chat/completions", headers=AUTH, json={"model": "missing", "messages": []})
    assert res.status_code == 404 and "missing" in res.json()["error"]["message"]


def test_needs_the_passphrase_from_the_lan(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        assert c.post(f"{BASE}/chat/completions", json={"model": "qwen3:8b", "messages": []}).status_code == 401
        assert c.get(f"{BASE}/models", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert FakeOllama.seen == [], "合言葉がなければ Ollama まで届かない"


def test_only_known_paths_are_relayed(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        res = c.post(f"{BASE}/../../api/pull", headers=AUTH, json={})
        assert res.status_code in (404, 405)
        res = c.post(f"{BASE}/files", headers=AUTH, json={})
        assert res.status_code == 404 and "files" in res.json()["error"]["message"]
        assert c.get(f"{BASE}/chat/completions", headers=AUTH).status_code == 404, "GET では送らない"
    assert FakeOllama.seen == []


def test_unreachable_tells_where_to_run_the_port_forward(tmp_path):
    with make(tmp_path, "http://127.0.0.1:9") as c:
        res = c.post(f"{BASE}/chat/completions", headers={**AUTH, "Accept-Language": "en"}, json={"model": "qwen3:8b", "messages": []})
    assert res.status_code == 502
    message = res.json()["error"]["message"]
    assert "keep the port forward running on the computer running the server" in message and "aws ssm start-session" in message


def test_unknown_environment(tmp_path, ollama):
    with make(tmp_path, ollama) as c:
        res = c.post("/api/environments/nope/openai/v1/chat/completions", headers=AUTH, json={})
    assert res.status_code == 404 and res.json()["error"]["message"]


def test_body_size_limit(tmp_path, ollama, monkeypatch):
    monkeypatch.setattr(openai_relay, "MAX_BODY_BYTES", 100)
    with make(tmp_path, ollama) as c:
        res = c.post(f"{BASE}/chat/completions", headers=AUTH, json={"model": "qwen3:8b", "messages": [{"role": "user", "content": "x" * 200}]})
    assert res.status_code == 413
