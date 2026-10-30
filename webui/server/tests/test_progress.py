"""起動の進み具合のテスト。terraform は偽物(fake_terraform.py)、AWS は決まった状態を返す偽物を使う"""

import datetime
import json
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.progress import build_progress, capacity_shortage
from llm_launchpad_webui.runner import Run, Runner
from llm_launchpad_webui.storage import startup_stage_sec

from test_app import events
from test_launch import VARIABLES

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime.datetime(2026, 10, 27, 3, 0, 0, tzinfo=datetime.timezone.utc)
TFVARS = {**VARIABLES, "model_storage": "s3_cache"}


def at(sec):
    """NOW から sec 秒前の時刻(インスタンスが書く形)"""
    return (NOW - datetime.timedelta(seconds=sec)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run(status="succeeded", started=600, finished=480, exit_code=0, lines=()):
    return Run(id="r1", command="launch", workspace="qwen3-8b", args=["apply"], status=status,
               exit_code=None if status == "running" else exit_code,
               started_at=at(started), finished_at=None if status == "running" else at(finished), lines=list(lines))


def statuses(progress):
    return {s["id"]: s["status"] for s in progress["stages"]}


def test_applying_is_the_first_stage():
    p = build_progress("qwen3-8b", TFVARS, make_run("running", started=30), None, 5.2, now=NOW)
    assert p["stage"] == "creating" and p["state"] == "running"
    assert statuses(p) == {"creating": "active", "booting": "pending", "installing_ollama": "pending",
                           "preparing_storage": "pending", "pulling_model": "pending", "verifying": "pending", "ready": "pending"}
    assert p["elapsed_sec"] == 30
    expected = startup_stage_sec(5.2, "s3_cache", cached=False)
    assert p["remaining_sec"] == round(sum(expected.values()) - 30), "分からない間は、取得し直す前提で見積もる"


def test_pulling_shows_percent_and_uses_the_real_speed():
    status = {
        "stage": "pulling_model", "state": "running",
        "stages": {"instance_started": at(200), "installing_ollama": at(190), "preparing_storage": at(170), "pulling_model": at(20)},
        "pull": {"percent": 40.0, "completed_bytes": 2, "total_bytes": 5, "message": "pulling"},
        "cache": {"hit": False, "bucket": "b"},
    }
    p = build_progress("qwen3-8b", TFVARS, make_run(), status, 5.2, now=NOW)
    assert p["stage"] == "pulling_model" and p["pull"]["percent"] == 40.0
    assert statuses(p)["preparing_storage"] == "done" and statuses(p)["verifying"] == "pending"
    by_id = {s["id"]: s for s in p["stages"]}
    assert by_id["booting"]["started_at"] == at(480), "起動は apply が終わったときから"
    assert by_id["booting"]["finished_at"] == at(190), "Ollama を入れ始めたら起動は終わり"
    # 20秒で40% → 残り60%は30秒。そのあとの応答の確認の目安を足す
    verifying = startup_stage_sec(5.2, "s3_cache", cached=False)["verifying"]
    assert p["remaining_sec"] == round(30 + verifying)


def test_ready():
    status = {"stage": "ready", "state": "ready", "ready_at": at(10), "stages": {"instance_started": at(300), "ready": at(10)}}
    p = build_progress("qwen3-8b", TFVARS, make_run(), status, 5.2, now=NOW)
    assert p["state"] == "ready" and set(statuses(p).values()) == {"done"}
    assert p["elapsed_sec"] == 590 and p["remaining_sec"] is None, "使えるようになるまでにかかった時間"


def test_instance_failure_shows_stage_error_and_log():
    status = {"stage": "pulling_model", "state": "failed", "error": "RuntimeError: モデルの取得に失敗しました",
              "log": "/var/log/llm-launchpad-bootstrap.log", "stages": {"pulling_model": at(65)}, "updated_at": at(60)}
    p = build_progress("qwen3-8b", TFVARS, make_run(), status, 5.2, now=NOW)
    assert statuses(p)["pulling_model"] == "failed" and statuses(p)["verifying"] == "pending"
    pulling = next(s for s in p["stages"] if s["id"] == "pulling_model")
    assert pulling["finished_at"] == at(60) and p["elapsed_sec"] == 540, "失敗したときで止める"
    assert p["error"] == {"stage": "pulling_model", "message": "RuntimeError: モデルの取得に失敗しました",
                          "log_path": "/var/log/llm-launchpad-bootstrap.log"}
    assert p["remaining_sec"] is None


def test_apply_failure_shows_terraform_log():
    lines = [f"line {i}" for i in range(100)]
    p = build_progress("qwen3-8b", TFVARS, make_run("failed", exit_code=1, lines=lines), None, 5.2, now=NOW)
    assert statuses(p)["creating"] == "failed" and p["elapsed_sec"] == 120, "apply が終わったときで止める"
    assert p["error"]["stage"] == "creating" and p["error"]["terraform_log"] == lines[-40:]


# 在庫がないAZで RunInstances が失敗したときの terraform の出力(-no-color、80桁で折り返した形)
CAPACITY_LOG = [
    "module.instance.aws_instance.this: Creating...",
    "Error: creating EC2 Instance: operation error EC2: RunInstances, exceeded maximum number of attempts, 5,",
    "https response error StatusCode: 500, RequestID: 0000, api error InsufficientInstanceCapacity: We currently",
    "do not have sufficient g6.xlarge capacity in the Availability Zone you requested (ap-northeast-1a). Our",
    "system will be working on provisioning additional capacity. You can currently get g6.xlarge capacity by not",
    "specifying an Availability Zone in your launch request or choosing ap-northeast-1c,",
    "ap-northeast-1d.",
    "",
    "  with module.instance.aws_instance.this,",
]


def test_capacity_shortage_reads_wrapped_message():
    assert capacity_shortage(CAPACITY_LOG) == {
        "instance_type": "g6.xlarge",
        "availability_zone": "ap-northeast-1a",
        "suggested_zones": ["ap-northeast-1c", "ap-northeast-1d"],
    }
    assert capacity_shortage(["Error: creating EC2 Instance: UnauthorizedOperation"]) is None


def test_apply_failure_by_capacity_asks_for_another_az():
    p = build_progress("qwen3-8b", TFVARS, make_run("failed", exit_code=1, lines=CAPACITY_LOG), None, 5.2, now=NOW)
    e = p["error"]
    assert e["code"] == "insufficient_capacity" and e["availability_zone"] == "ap-northeast-1a"
    assert "ap-northeast-1a" in e["message"] and "g6.xlarge" in e["message"] and "別のAZ" in e["message"]
    assert e["terraform_log"] == CAPACITY_LOG, "terraform の出力も出す"

    ebs = build_progress("qwen3-8b", {**TFVARS, "model_storage": "ebs"}, make_run("failed", exit_code=1, lines=CAPACITY_LOG), None, 5.2, now=NOW)
    assert ebs["error"]["code"] == "insufficient_capacity" and "EBS永続" in ebs["error"]["message"], "EBS永続はAZを変えられない"


def test_without_run_uses_instance_times():
    """サーバーを起動し直して apply の記録がなくても、インスタンスが書いた状態から出す"""
    status = {"stage": "verifying", "state": "running", "started_at": at(300), "stages": {"instance_started": at(300), "verifying": at(30)}}
    p = build_progress("qwen3-8b", TFVARS, None, status, 5.2, now=NOW)
    assert statuses(p)["creating"] == "done" and statuses(p)["verifying"] == "active"
    assert p["started_at"] == at(300) and p["elapsed_sec"] == 300


class FakeAwsInfo:
    def __init__(self, status=None, fail=False):
        self.status = status
        self.fail = fail
        self.calls = 0

    def offering_azs(self, region, instance_type):
        return ["ap-northeast-1a", "ap-northeast-1c", "ap-northeast-1d"]

    def subnet_azs(self, region):
        return ["ap-northeast-1a", "ap-northeast-1c", "ap-northeast-1d"]

    def bootstrap_status(self, region, env_id):
        self.calls += 1
        if self.fail:
            raise AwsInfoError("aws.fetch_failed", what="bootstrap_status", error="AccessDenied")
        return self.status


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    return d


@pytest.fixture
def aws():
    return FakeAwsInfo()


@pytest.fixture
def client(tmp_path, terraform_dir, aws):
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    with TestClient(create_app(Runner(str(terraform_dir), str(fake)), web_dist=None, aws_info=aws)) as c:
        yield c


def launch_and_wait(client):
    run = client.post("/api/launch", json={"workspace": "qwen3-8b", "variables": VARIABLES}).json()
    events(client, run["id"])
    return run


def test_api_ready_adds_connection(client, aws):
    launch_and_wait(client)
    aws.status = {"stage": "ready", "state": "ready", "ready_at": at(0), "stages": {"ready": at(0)}}
    p = client.get("/api/environments/qwen3-8b/progress").json()
    assert p["state"] == "ready" and p["model"] == "qwen3:8b"
    assert p["connection"]["ssm_port_forward_command"].endswith("# qwen3-8b"), "その環境の terraform output を読む"
    assert "vpc_id" not in p["connection"], "接続のしかただけを出す"
    assert client.get("/api/environments/qwen3-8b/progress").json()["connection"] == p["connection"]


def test_api_running_has_no_connection(client, aws):
    launch_and_wait(client)
    aws.status = {"stage": "installing_ollama", "state": "running", "stages": {"installing_ollama": at(0)}}
    p = client.get("/api/environments/qwen3-8b/progress").json()
    assert p["stage"] == "installing_ollama" and p["connection"] is None


def test_api_does_not_read_old_status_while_applying(client, aws, monkeypatch):
    """apply の途中は、前に起動したときの状態が残っていることがあるので読まない"""
    aws.status = {"stage": "ready", "state": "ready", "stages": {}}
    run = client.post("/api/launch", json={"workspace": "qwen3-8b", "variables": VARIABLES}).json()
    p = client.get("/api/environments/qwen3-8b/progress").json()
    if p["run"]["status"] == "running":
        assert p["stage"] == "creating" and aws.calls == 0
    events(client, run["id"])


def test_api_aws_error_is_warning(client, aws):
    launch_and_wait(client)
    aws.fail = True
    p = client.get("/api/environments/qwen3-8b/progress").json()
    assert p["stage"] == "booting" and "AccessDenied" in p["warnings"][0]


def test_api_output_failure_is_warning(client, aws, monkeypatch):
    launch_and_wait(client)
    monkeypatch.setenv("FAKE_TERRAFORM_FAIL", "output")
    aws.status = {"stage": "verifying", "state": "failed", "error": "boom", "stages": {}}
    p = client.get("/api/environments/qwen3-8b/progress").json()
    assert p["error"]["message"] == "boom" and p["connection"] is None
    assert "No outputs found" in p["warnings"][0]


def test_api_unknown_environment(client, terraform_dir):
    assert client.get("/api/environments/nothing/progress").status_code == 404
    assert client.get("/api/environments/default/progress").status_code == 422
    # tfvars だけあれば(サーバーを起動し直したあと)、状態を読みに行く
    (terraform_dir / "tfvars").mkdir()
    (terraform_dir / "tfvars" / "old-env.tfvars.json").write_text(json.dumps(TFVARS))
    assert client.get("/api/environments/old-env/progress").json()["state"] == "unknown"


def test_api_environments_newest_first(client, terraform_dir):
    assert client.get("/api/environments").json() == {"environments": []}
    d = terraform_dir / "tfvars"
    d.mkdir()
    for i, name in enumerate(["old-env", "new-env", "default"]):
        (d / f"{name}.tfvars.json").write_text("{}")
        os.utime(d / f"{name}.tfvars.json", (1000 + i, 1000 + i))
    assert client.get("/api/environments").json() == {"environments": ["new-env", "old-env"]}


def fail_by_capacity(client, monkeypatch, variables=VARIABLES):
    monkeypatch.setenv("FAKE_TERRAFORM_FAIL", "apply")
    monkeypatch.setenv("FAKE_TERRAFORM_ERROR", "\n".join(CAPACITY_LOG))
    run = client.post("/api/launch", json={"workspace": "qwen3-8b", "variables": variables}).json()
    events(client, run["id"])
    monkeypatch.delenv("FAKE_TERRAFORM_FAIL")


def test_api_capacity_failure_lists_other_zones(client, monkeypatch):
    fail_by_capacity(client, monkeypatch)
    e = client.get("/api/environments/qwen3-8b/progress").json()["error"]
    assert e["code"] == "insufficient_capacity"
    assert e["zones"] == ["ap-northeast-1c", "ap-northeast-1d"], "在庫がなかったAZは除き、AWS がすすめるAZを先にする"


def test_api_relaunch_changes_only_the_az(client, terraform_dir, monkeypatch):
    fail_by_capacity(client, monkeypatch)
    res = client.post("/api/environments/qwen3-8b/relaunch", json={"availability_zone": "ap-northeast-1c"})
    assert res.status_code == 201, res.text
    assert [s[0] for s in res.json()["steps"]] == ["init", "apply"]
    events(client, res.json()["id"])
    written = json.loads((terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").read_text())
    assert written == {**VARIABLES, "availability_zone": "ap-northeast-1c"}
    assert client.get("/api/environments/qwen3-8b/progress").json()["state"] != "failed"

    # 自動に戻すと、tfvars から AZ を消す
    res = client.post("/api/environments/qwen3-8b/relaunch", json={"availability_zone": None})
    events(client, res.json()["id"])
    assert "availability_zone" not in json.loads((terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").read_text())


def test_api_relaunch_rejects_bad_input(client, monkeypatch):
    assert client.post("/api/environments/nothing/relaunch", json={"availability_zone": "ap-northeast-1c"}).status_code == 422
    fail_by_capacity(client, monkeypatch, {**VARIABLES, "model_storage": "ebs"})
    res = client.post("/api/environments/qwen3-8b/relaunch", json={"availability_zone": "ap-northeast-1c"})
    assert res.status_code == 422 and "EBS" in res.json()["detail"]
    res = client.post("/api/environments/qwen3-8b/relaunch", json={"availability_zone": "1c; rm -rf /"})
    assert res.status_code == 422
    e = client.get("/api/environments/qwen3-8b/progress").json()["error"]
    assert "zones" not in e, "EBS永続ではAZを選ばせない"
