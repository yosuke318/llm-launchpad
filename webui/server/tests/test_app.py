"""WebUI の API のテスト。terraform は偽物(fake_terraform.py)を使う

    cd webui/server && uv run pytest
"""

import json
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.runner import Runner

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def terraform_dir(tmp_path):
    return tmp_path / "terraform"


@pytest.fixture
def client(tmp_path, terraform_dir):
    terraform_dir.mkdir()
    # 偽の terraform を、今の Python で動くようにして置く
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    app = create_app(Runner(str(terraform_dir), str(fake)), web_dist=None)
    with TestClient(app) as c:
        yield c


def events(client, run_id, start=0, headers=None):
    """SSE を最後まで読んで、(event, data) の並びにする"""
    out = []
    with client.stream("GET", f"/api/runs/{run_id}/events", params={"start": start}, headers=headers or {}) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in res.iter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                out.append((event, json.loads(line.removeprefix("data: "))))
    return out


def log_lines(evs):
    return [d["line"] for e, d in evs if e == "log"]


def test_run_streams_output_and_end(client):
    res = client.post("/api/runs", json={"command": "init", "workspace": "default"})
    assert res.status_code == 201
    run = res.json()
    assert run["args"] == ["init", "-input=false", "-no-color"]

    evs = events(client, run["id"])
    lines = log_lines(evs)
    assert lines[0] == "fake terraform init -input=false -no-color"
    assert lines[1] == "workspace=default automation=1", "TF_WORKSPACE と TF_IN_AUTOMATION を渡すこと"
    assert lines[-1] == "line 2 日本語", "日本語の出力もそのまま流すこと"
    assert evs[-1] == ("end", {"status": "succeeded", "exit_code": 0})


def test_failed_command_reports_exit_code(client):
    run = client.post("/api/runs", json={"command": "validate"}).json()
    evs = events(client, run["id"])
    assert evs[-1] == ("end", {"status": "failed", "exit_code": 1})
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "failed"


def test_resume_from_line(client):
    run = client.post("/api/runs", json={"command": "version"}).json()
    all_lines = log_lines(events(client, run["id"]))
    assert log_lines(events(client, run["id"], start=2)) == all_lines[2:], "つなぎ直したときは続きから受け取れること"
    # ブラウザの EventSource は、最後に受け取った id(行番号)を Last-Event-ID で送ってくる
    resumed = log_lines(events(client, run["id"], headers={"Last-Event-ID": "1"}))
    assert resumed == all_lines[2:], "Last-Event-ID の次の行から送ること"


def test_plan_writes_var_file(client, terraform_dir):
    variables = {"model": "qwen3:8b", "model_storage": "download", "allowed_cidrs": []}
    run = client.post("/api/runs", json={"command": "plan", "workspace": "qwen-test", "variables": variables}).json()
    assert run["args"][-1] == "-var-file=tfvars/qwen-test.tfvars.json"

    lines = log_lines(events(client, run["id"]))
    assert "workspace=qwen-test automation=1" in lines
    written = json.loads((terraform_dir / "tfvars" / "qwen-test.tfvars.json").read_text())
    assert written == variables, "画面の値を tfvars に書き、terraform 単体でも同じことができるようにすること"


def test_rejects_unknown_command(client):
    assert client.post("/api/runs", json={"command": "destroy"}).status_code == 422


def test_rejects_invalid_workspace(client):
    res = client.post("/api/runs", json={"command": "plan", "workspace": "../etc"})
    assert res.status_code == 422


def test_rejects_variables_for_other_commands(client):
    res = client.post("/api/runs", json={"command": "init", "variables": {"model": "qwen3:8b"}})
    assert res.status_code == 422


def test_only_one_run_at_a_time(client):
    first = client.post("/api/runs", json={"command": "init"}).json()
    second = client.post("/api/runs", json={"command": "plan"})
    assert second.status_code == 409, "init は .terraform を書き換えるため、同時に1つだけ動かすこと"
    events(client, first["id"])
    third = client.post("/api/runs", json={"command": "plan"})
    assert third.status_code == 201, "終われば次を実行できること"
    events(client, third.json()["id"])


def test_list_runs_and_unknown_run(client):
    run = client.post("/api/runs", json={"command": "version"}).json()
    events(client, run["id"])
    assert client.get("/api/runs").json()["runs"][0]["id"] == run["id"]
    assert client.get("/api/runs/nope").status_code == 404


def test_workspaces(client):
    assert client.get("/api/workspaces").json() == {
        "workspaces": [{"name": "default", "current": True}, {"name": "qwen-test", "current": False}],
    }


def test_missing_terraform_binary(tmp_path):
    app = create_app(Runner(str(tmp_path), str(tmp_path / "no-such-terraform")), web_dist=None)
    with TestClient(app) as c:
        run = c.post("/api/runs", json={"command": "version"}).json()
        evs = events(c, run["id"])
    assert "terraform を実行できませんでした" in log_lines(evs)[0]
    assert evs[-1] == ("end", {"status": "failed", "exit_code": -1})
