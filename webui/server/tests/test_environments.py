"""環境の一覧と停止・削除のテスト。terraform は偽物(fake_terraform.py)、AWS は決まったインスタンスとタグを返す偽物を使う"""

import datetime
import json
import os
import stat
import sys

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.environments import arn_resource_type, drop_deleted, find_leftovers, state_identifiers
from llm_launchpad_webui.runner import Runner

from test_app import events, log_lines
from test_launch import VARIABLES

HERE = os.path.dirname(os.path.abspath(__file__))
REGION = "ap-northeast-1"
ACCOUNT = "123456789012"


def state_with_instance(instance_id="i-0aaa", root_volume="vol-0root", region=REGION):
    return {
        "version": 4,
        "resources": [
            {"mode": "data", "type": "aws_ami", "name": "dlami", "instances": [{"attributes": {"id": "ami-1"}}]},
            {"mode": "managed", "type": "aws_instance", "name": "this", "module": "module.instance", "instances": [{"attributes": {
                "id": instance_id, "arn": f"arn:aws:ec2:{region}:{ACCOUNT}:instance/{instance_id}", "region": region,
                "instance_type": "g6.xlarge", "availability_zone": f"{region}a",
                "root_block_device": [{"volume_id": root_volume}],
            }}]},
            {"mode": "managed", "type": "aws_vpc", "name": "this", "instances": [{"attributes": {
                "id": "vpc-0aaa", "arn": f"arn:aws:ec2:{region}:{ACCOUNT}:vpc/vpc-0aaa",
            }}]},
        ],
    }


def instance(id="i-0aaa", state="running", env_id="qwen3-8b", purchase="spot", launched_ago=3600):
    launched = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=launched_ago)
    return {"id": id, "state": state, "instance_type": "g6.xlarge", "availability_zone": f"{REGION}a",
            "launch_time": launched.isoformat(), "purchase_option": purchase, "env_id": env_id}


def tagged(arn, **tags):
    return {"arn": arn, "tags": {"Project": "llm-launchpad", **tags}}


class FakeAwsInfo:
    def __init__(self):
        self.instances = []
        self.tagged = []
        self.fail = False
        # destroy で消したのに、タグ付けの API がまだ返す ARN
        self.gone = set()
        # 今もあるか確かめられない種類
        self.check_fail = set()
        self.stopped = []
        self.started = []

    def project_instances(self, region):
        if self.fail:
            raise AwsInfoError("aws.fetch_failed", what="project_instances", error="AccessDenied")
        return [i for i in self.instances if region == REGION]

    def tagged_resources(self, region):
        if self.fail:
            raise AwsInfoError("aws.fetch_failed", what="tagged_resources", error="AccessDenied")
        return self.tagged if region == REGION else []

    def existing_arns(self, region, kind, arns):
        if kind in self.check_fail:
            raise AwsInfoError("aws.existence_check_failed", region=region, kind=kind, error="UnauthorizedOperation")
        if kind == "ec2:instance":
            return None
        return {a for a in arns if a not in self.gone}

    def spot_hourly_by_az(self, region, instance_type):
        return {f"{REGION}a": 0.36}

    def on_demand_hourly(self, region, instance_type):
        return 1.2

    def stop_instance(self, region, instance_id):
        self.stopped.append(instance_id)
        return "stopping"

    def start_instance(self, region, instance_id):
        self.started.append(instance_id)
        return "pending"


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    (d / "fake-state").mkdir(parents=True)
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


def write_state(terraform_dir, ws, state):
    (terraform_dir / "fake-state" / f"{ws}.json").write_text(json.dumps(state))


def write_tfvars(terraform_dir, ws, variables=VARIABLES):
    (terraform_dir / "tfvars").mkdir(exist_ok=True)
    (terraform_dir / "tfvars" / f"{ws}.tfvars.json").write_text(json.dumps(variables))


def test_state_identifiers_include_nested_ids():
    ids = state_identifiers(state_with_instance())
    assert {"i-0aaa", "vol-0root", "vpc-0aaa"} <= ids and "ami-1" not in ids, "data ソースは作ったものではない"


def test_arn_resource_type():
    assert arn_resource_type(f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-1") == "ec2:instance"
    assert arn_resource_type(f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/llm-launchpad/x/status") == "ssm:parameter"
    assert arn_resource_type(f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/default/llm-lp-x-1") == "scheduler:schedule"


def test_leftovers_skip_persistent_known_and_terminated():
    arn = lambda kind, rid: f"arn:aws:ec2:{REGION}:{ACCOUNT}:{kind}/{rid}"
    resources = [
        {**tagged(arn("vpc", "vpc-0aaa"), EnvId="qwen3-8b"), "region": REGION},
        {**tagged(arn("volume", "vol-0models"), EnvId="model-store", Persistent="true"), "region": REGION},
        {**tagged(arn("instance", "i-0gone"), EnvId="old"), "region": REGION},
        {**tagged(arn("instance", "i-0left"), EnvId="old", Name="llm-launchpad-old"), "region": REGION},
        {**tagged(arn("vpc", "vpc-0left"), EnvId="old"), "region": REGION},
        {**tagged(arn("vpc", "vpc-0busy"), EnvId="applying"), "region": REGION},
    ]
    leftovers = find_leftovers(resources, {"vpc-0aaa"}, {"i-0left"}, skip_env_ids={"applying"})
    assert [(x["resource_type"], x["resource_id"]) for x in leftovers] == [("ec2:instance", "i-0left"), ("ec2:vpc", "vpc-0left")]
    assert leftovers[0]["name"] == "llm-launchpad-old" and leftovers[0]["env_id"] == "old"


def test_overview_lists_environment_with_cost(client, terraform_dir, aws):
    write_state(terraform_dir, "qwen3-8b", state_with_instance())
    write_tfvars(terraform_dir, "qwen3-8b")
    aws.instances = [instance()]
    aws.tagged = [tagged(f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0aaa", EnvId="qwen3-8b")]
    res = client.get("/api/environments/overview").json()
    assert [e["workspace"] for e in res["environments"]] == ["qwen3-8b"], "state が空の qwen-test と default は出さない"
    env = res["environments"][0]
    assert env["model"] == "qwen3:8b" and env["instance_type"] == "g6.xlarge" and env["region"] == REGION
    assert env["instance"]["state"] == "running" and env["resource_count"] == 2
    assert env["hourly_usd"] == 0.36, "スポットは、その AZ のスポット価格"
    assert 3590 <= env["elapsed_sec"] <= 3610 and env["cost_usd"] == pytest.approx(0.36, abs=0.01)
    assert res["leftovers"] == [] and res["regions"] == [REGION]


def test_overview_stopped_has_no_cost(client, terraform_dir, aws):
    write_state(terraform_dir, "qwen3-8b", state_with_instance())
    write_tfvars(terraform_dir, "qwen3-8b")
    aws.instances = [instance(state="stopped")]
    env = client.get("/api/environments/overview").json()["environments"][0]
    assert env["instance"]["state"] == "stopped" and env["cost_usd"] is None and env["elapsed_sec"] is None


def test_overview_shows_leftovers(client, terraform_dir, aws):
    aws.instances = [instance(id="i-0left", env_id="old")]
    aws.tagged = [
        tagged(f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0left", EnvId="old"),
        tagged(f"arn:aws:s3:::llm-launchpad-models-{ACCOUNT}-{REGION}", EnvId="model-store", Persistent="true"),
    ]
    res = client.get("/api/environments/overview").json()
    assert res["environments"] == []
    assert [(x["resource_id"], x["instance_state"]) for x in res["leftovers"]] == [("i-0left", "running")]


def test_drop_deleted_checks_per_kind():
    arn = lambda kind, rid: f"arn:aws:ec2:{REGION}:{ACCOUNT}:{kind}/{rid}"
    resources = [{"region": REGION, "arn": a} for a in [
        arn("vpc", "vpc-0gone"), arn("subnet", "subnet-0left"), arn("vpc", "vpc-0left"),
        arn("security-group", "sg-0unknown"), f"arn:aws:scheduler:{REGION}:{ACCOUNT}:schedule/default/x",
    ]]
    calls = []

    def existing(region, kind, arns):
        calls.append((region, kind, arns))
        if kind == "ec2:security-group":
            raise AwsInfoError("aws.existence_check_failed", region=region, kind=kind, error="Throttling")
        if kind == "scheduler:schedule":
            return None
        return {a for a in arns if "gone" not in a}

    kept, errors = drop_deleted(resources, existing)
    assert [r["arn"] for r in kept] == [r["arn"] for r in resources[1:]], "消えたものだけ除き、並びは変えない"
    assert [e.params["kind"] for e in errors] == ["ec2:security-group"], "確かめられなかった種類はそのまま残す"
    assert sorted(c[1] for c in calls) == ["ec2:security-group", "ec2:subnet", "ec2:vpc", "scheduler:schedule"], "種類ごとに1回"
    assert ("ec2:vpc", [arn("vpc", "vpc-0gone"), arn("vpc", "vpc-0left")]) in [(c[1], c[2]) for c in calls]


def test_overview_hides_destroyed_resources(client, terraform_dir, aws):
    """destroy したあとタグ付けの API がまだ返すものは、消し忘れに出さない"""
    vpc = f"arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-0old"
    volume = f"arn:aws:ec2:{REGION}:{ACCOUNT}:volume/vol-0left"
    param = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/llm-launchpad/old/status"
    aws.tagged = [tagged(vpc, EnvId="old"), tagged(volume, EnvId="old"), tagged(param, EnvId="old")]
    aws.gone = {vpc, param}
    res = client.get("/api/environments/overview").json()
    assert [x["arn"] for x in res["leftovers"]] == [volume]
    assert res["warnings"] == []


def test_overview_unverified_leftovers_stay_with_warning(client, terraform_dir, aws):
    vpc = f"arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-0old"
    aws.tagged = [tagged(vpc, EnvId="old")]
    aws.gone = {vpc}
    aws.check_fail = {"ec2:vpc"}
    res = client.get("/api/environments/overview", headers={"Accept-Language": "en"}).json()
    assert [x["arn"] for x in res["leftovers"]] == [vpc], "確かめられないときは、今までどおり出す"
    assert res["warnings"] == [
        f"Could not check whether the ec2:vpc resources in {REGION} still exist, "
        "so already deleted ones may be listed as leftovers: UnauthorizedOperation"
    ]


def test_overview_state_error_does_not_mark_leftovers(client, terraform_dir, aws, monkeypatch):
    write_tfvars(terraform_dir, "qwen3-8b")
    aws.tagged = [tagged(f"arn:aws:ec2:{REGION}:{ACCOUNT}:vpc/vpc-0aaa", EnvId="qwen3-8b")]
    monkeypatch.setenv("FAKE_TERRAFORM_FAIL", "state")
    res = client.get("/api/environments/overview").json()
    assert res["leftovers"] == [], "state を読めない環境のリソースは、消し忘れと決めない"
    assert res["environments"][0]["state_error"] and "Failed to load state" in res["warnings"][0]


def test_overview_aws_error_is_warning(client, terraform_dir, aws):
    write_state(terraform_dir, "qwen3-8b", state_with_instance())
    write_tfvars(terraform_dir, "qwen3-8b")
    aws.fail = True
    res = client.get("/api/environments/overview").json()
    assert res["environments"][0]["instance"] is None and res["unchecked_regions"] == [REGION]
    assert any("AccessDenied" in w for w in res["warnings"])


def test_stop_and_start(client, terraform_dir, aws):
    write_state(terraform_dir, "qwen3-8b", state_with_instance())
    aws.instances = [instance()]
    assert client.post("/api/environments/qwen3-8b/stop").json() == {"instance_id": "i-0aaa", "state": "stopping"}
    assert client.post("/api/environments/qwen3-8b/start").json()["state"] == "pending"
    assert aws.stopped == ["i-0aaa"] and aws.started == ["i-0aaa"]


def test_stop_checks_env_tag(client, terraform_dir, aws):
    """state にあるインスタンスでも、その環境のタグが付いていなければ止めない"""
    write_state(terraform_dir, "qwen3-8b", state_with_instance())
    aws.instances = [instance(env_id="other")]
    assert client.post("/api/environments/qwen3-8b/stop").status_code == 404
    assert client.post("/api/environments/nothing/stop").status_code == 404
    assert aws.stopped == []


def test_destroy_runs_and_removes_tfvars(client, terraform_dir):
    write_tfvars(terraform_dir, "qwen3-8b")
    run = client.post("/api/environments/qwen3-8b/destroy")
    assert run.status_code == 201
    body = run.json()
    assert body["command"] == "destroy" and body["steps"][0][0] == "init"
    assert body["args"][:4] == ["destroy", "-input=false", "-no-color", "-auto-approve"]
    evs = events(client, body["id"])
    assert evs[-1][1]["status"] == "succeeded" and any("workspace=qwen3-8b" in line for line in log_lines(evs))
    assert not (terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").exists(), "消した環境は一覧に出さない"


def test_destroy_failure_keeps_tfvars(client, terraform_dir, monkeypatch):
    write_tfvars(terraform_dir, "qwen3-8b")
    monkeypatch.setenv("FAKE_TERRAFORM_FAIL", "destroy")
    run = client.post("/api/environments/qwen3-8b/destroy").json()
    end = events(client, run["id"])[-1][1]
    assert end["status"] == "failed" and (terraform_dir / "tfvars" / "qwen3-8b.tfvars.json").exists(), "やり直せるよう残す"


def test_destroy_needs_tfvars(client):
    res = client.post("/api/environments/manual/destroy")
    assert res.status_code == 422 and "terraform destroy" in res.json()["detail"]
    assert client.post("/api/environments/default/destroy").status_code == 422
