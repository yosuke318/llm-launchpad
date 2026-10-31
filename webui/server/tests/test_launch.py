"""起動(/api/launch)のテスト。terraform は偽物(fake_terraform.py)を使う"""

import json
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.runner import Runner

from test_app import events, log_lines

HERE = os.path.dirname(os.path.abspath(__file__))

VARIABLES = {
    "model": "qwen3:8b",
    "region": "ap-northeast-1",
    "context_length": 8192,
    "kv_cache_type": "f16",
    "model_storage": "download",
    "network_mode": "public",
    "purchase_option": "spot",
    "idle_stop_minutes": 30,
    "max_lifetime_hours": 8,
}


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    return d


@pytest.fixture
def client(tmp_path, terraform_dir):
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    with TestClient(create_app(Runner(str(terraform_dir), str(fake)), web_dist=None)) as c:
        yield c


def launch(client, workspace="qwen3-8b", variables=None):
    return client.post("/api/launch", json={"workspace": workspace, "variables": variables or VARIABLES})


def test_launch_writes_tfvars_and_runs_init_then_apply(client, terraform_dir):
    res = launch(client)
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["command"] == "launch"
    assert [s[0] for s in run["steps"]] == ["init", "apply"]
    assert run["args"] == ["apply", "-input=false", "-no-color", "-auto-approve", "-var-file=tfvars/qwen3-8b.tfvars.json"]

    evs = events(client, run["id"])
    lines = log_lines(evs)
    assert lines[0] == "$ terraform init -input=false -no-color", "コマンドの区切りが分かるようにする"
    assert "$ terraform apply -input=false -no-color -auto-approve -var-file=tfvars/qwen3-8b.tfvars.json" in lines
    assert "workspace=qwen3-8b automation=1" in lines, "TF_WORKSPACE で環境を選ぶ"
    assert evs[-1] == ("end", {"status": "succeeded", "exit_code": 0})

    written = json.loads((terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").read_text())
    assert written == VARIABLES, "表示した内容を tfvars に書く"


def test_launch_stops_when_init_fails(client, monkeypatch):
    monkeypatch.setenv("FAKE_TERRAFORM_FAIL", "init")
    run = launch(client).json()
    evs = events(client, run["id"])
    assert evs[-1] == ("end", {"status": "failed", "exit_code": 1})
    assert not any(line.startswith("$ terraform apply") for line in log_lines(evs)), "init が失敗したら apply しない"


def test_launch_drops_null_values(client, terraform_dir):
    run = launch(client, variables={**VARIABLES, "instance_type": None}).json()
    events(client, run["id"])
    assert "instance_type" not in json.loads((terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").read_text())


@pytest.mark.parametrize("workspace,variables", [
    ("default", VARIABLES),
    ("Bad_Name", VARIABLES),
    ("qwen3-8b", {**VARIABLES, "vpc_cidr": "0.0.0.0/0"}),
    ("qwen3-8b", {**VARIABLES, "idle_stop_minutes": "30"}),
    ("qwen3-8b", {**VARIABLES, "idle_stop_minutes": True}),
    ("qwen3-8b", {**VARIABLES, "model": "no-such:1b"}),
    ("qwen3-8b", {k: v for k, v in VARIABLES.items() if k != "model"}),
])
def test_launch_rejects_invalid_input(client, workspace, variables):
    assert launch(client, workspace, variables).status_code == 422


def test_launch_is_busy_while_another_run(client):
    first = launch(client).json()
    assert launch(client, "other-env").status_code == 409
    events(client, first["id"])


def test_runs_api_still_cannot_apply(client):
    assert client.post("/api/runs", json={"command": "apply"}).status_code == 422
