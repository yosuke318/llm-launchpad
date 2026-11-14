"""比較モードのテスト。順に起動して測って削除する流れは偽の ops で、API は偽の terraform と AWS で確かめる"""

import asyncio
import csv
import datetime
import io
import json
import os
import stat
import sys
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui import comparison as comparisons
from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.comparison import COMPARISON_CSV_COLUMNS, ComparisonError
from llm_launchpad_webui.messages import MessageError
from llm_launchpad_webui.runner import BusyError, Runner

from test_benchmark import FakeBenchAws

HERE = os.path.dirname(os.path.abspath(__file__))

REQUEST = {
    "model": "qwen3:8b",
    "region": "ap-northeast-1",
    "context_length": 8192,
    "kv_cache_type": "f16",
    "network_mode": "public",
    "runs": 1,
    "idle_stop_minutes": 30,
    "max_lifetime_hours": 2,
    "configs": [
        {"instance_type": "g4dn.xlarge", "model_storage": "s3_cache", "purchase_option": "spot"},
        {"instance_type": "g6.xlarge", "model_storage": "ebs", "purchase_option": "on-demand"},
    ],
}

FITTING = ["g4dn.xlarge", "g6.xlarge", "g5.xlarge"]


# 順に起動して測って削除する流れ(comparison.run)


class FakeOps:
    """起動、削除、進み具合、計測を記録する。fail_* に環境IDを入れると、その環境で失敗させる"""

    def __init__(self):
        self.calls = []
        self.fail_apply = set()
        self.fail_ready = set()
        self.fail_bench = set()
        self.fail_destroy = set()
        self.busy = 0
        self.progress_until_ready = 1
        self.polls = {}
        self.on_progress = None

    def _run(self, status):
        return SimpleNamespace(id=f"r{len(self.calls)}", status=status)

    async def launch(self, workspace, variables):
        if self.busy:
            self.busy -= 1
            raise BusyError("busy")
        self.calls.append(("launch", workspace, variables))
        return self._run("failed" if workspace in self.fail_apply else "succeeded")

    async def destroy(self, workspace):
        self.calls.append(("destroy", workspace))
        return self._run("failed" if workspace in self.fail_destroy else "succeeded")

    async def wait(self, run):
        return None

    def is_busy_error(self, e):
        return isinstance(e, BusyError)

    async def progress(self, workspace):
        if self.on_progress:
            self.on_progress(workspace)
        launch = next(c for c in reversed(self.calls) if c[0] == "launch" and c[1] == workspace)
        if workspace in self.fail_apply:
            return {"state": "failed", "stage": "creating", "error": {"stage": "creating", "message": "apply が失敗しました"}}
        if workspace in self.fail_ready:
            return {"state": "failed", "stage": "pulling_model", "error": {"stage": "pulling_model", "message": "disk full"}}
        self.polls[workspace] = self.polls.get(workspace, 0) + 1
        if self.polls[workspace] < self.progress_until_ready:
            return {"state": "running", "stage": "pulling_model"}
        assert launch
        return {"state": "ready", "stage": "ready", "elapsed_sec": 420}

    async def benchmark(self, workspace, comparison, config):
        self.calls.append(("benchmark", workspace))
        if workspace in self.fail_bench:
            raise ComparisonError("comparison.bench_unavailable", error="SSM に届きません")
        return {"id": f"b-{workspace}", "hourly_usd": 0.36, "output_tokens_per_sec": 40.0, "comparison_id": comparison["id"]}


def make(request=REQUEST):
    return comparisons.new_comparison(request)


def go(comparison, ops, **kw):
    asyncio.run(comparisons.run(comparison, ops, poll_sec=0, **kw))


def kinds(ops):
    return [(c[0], c[1]) for c in ops.calls]


def test_runs_each_config_in_order_and_destroys_before_the_next():
    c, ops = make(), FakeOps()
    go(c, ops)
    ws1, ws2 = (cfg["workspace"] for cfg in c["configs"])
    assert kinds(ops) == [("launch", ws1), ("benchmark", ws1), ("destroy", ws1),
                          ("launch", ws2), ("benchmark", ws2), ("destroy", ws2)], "同時に動かす環境は1つだけ"
    assert c["status"] == "succeeded" and c["finished_at"]
    for cfg in c["configs"]:
        assert cfg["status"] == "done" and cfg["destroyed"] is True and cfg["startup_sec"] == 420
        assert cfg["result"]["comparison_id"] == c["id"]
        assert cfg["run_cost_usd"] == comparisons.run_cost(0.36, cfg["elapsed_sec"])


def test_tfvars_differ_only_in_the_compared_values():
    c, ops = make(), FakeOps()
    go(c, ops)
    launched = [v for kind, _, *rest in ops.calls if kind == "launch" for v in rest]
    assert launched[0] == {
        "model": "qwen3:8b", "region": "ap-northeast-1", "context_length": 8192, "kv_cache_type": "f16", "network_mode": "public",
        "idle_stop_minutes": 30, "max_lifetime_hours": 2,
        "instance_type": "g4dn.xlarge", "model_storage": "s3_cache", "purchase_option": "spot",
    }
    assert {k: v for k, v in launched[1].items() if launched[0][k] != v} == {
        "instance_type": "g6.xlarge", "model_storage": "ebs", "purchase_option": "on-demand"}
    assert "availability_zone" not in launched[1], "AZ は terraform に選ばせる"


def test_workspace_names_follow_the_env_id_rule():
    c = make()
    assert [cfg["workspace"] for cfg in c["configs"]] == [f"cmp-{c['id'][:6]}-1", f"cmp-{c['id'][:6]}-2"]
    from llm_launchpad_webui.runner import WORKSPACE_PATTERN
    assert all(WORKSPACE_PATTERN.match(cfg["workspace"]) for cfg in c["configs"])


@pytest.mark.parametrize("failure", ["fail_apply", "fail_ready", "fail_bench"])
def test_a_failed_config_is_still_destroyed_and_the_next_one_runs(failure):
    c, ops = make(), FakeOps()
    ws1, ws2 = (cfg["workspace"] for cfg in c["configs"])
    getattr(ops, failure).add(ws1)
    go(c, ops)
    assert ("destroy", ws1) in kinds(ops), "失敗しても、作ったものは消す"
    assert ("launch", ws2) in kinds(ops) and c["configs"][1]["status"] == "done"
    first = c["configs"][0]
    assert first["status"] == "failed" and first["destroyed"] is True and isinstance(first["error"], MessageError)
    assert c["status"] == "partial"


def test_launch_failure_message_names_the_stage():
    c, ops = make(), FakeOps()
    ops.fail_ready.add(c["configs"][0]["workspace"])
    go(c, ops)
    assert str(c["configs"][0]["error"]) == "起動が「pulling_model」の段階で失敗しました: disk full"


def test_stops_when_destroy_fails_so_environments_do_not_pile_up():
    c, ops = make(), FakeOps()
    ws1, ws2 = (cfg["workspace"] for cfg in c["configs"])
    ops.fail_destroy.add(ws1)
    go(c, ops)
    assert kinds(ops).count(("destroy", ws1)) == comparisons.DESTROY_ATTEMPTS, "もう一度だけ試す"
    assert ("launch", ws2) not in kinds(ops), "消せない環境があるまま次を起動しない"
    assert c["status"] == "failed" and ws1 in str(c["error"])
    assert c["configs"][0]["status"] == "failed" and c["configs"][0]["destroyed"] is False
    assert c["configs"][1]["status"] == "skipped"


def test_waits_for_other_terraform_runs_before_launching():
    c, ops = make(), FakeOps()
    ops.busy = 2
    go(c, ops)
    assert c["status"] == "succeeded"


def test_gives_up_when_other_runs_do_not_finish():
    c, ops = make(), FakeOps()
    ops.busy = 10_000
    go(c, ops, busy_timeout_sec=0)
    assert all(cfg["status"] == "failed" for cfg in c["configs"])
    assert not any(k == "destroy" for k, _ in kinds(ops)), "起動していなければ消すものはない"
    assert "終わらなかった" in str(c["configs"][0]["error"])


def test_ready_timeout_fails_the_config_and_destroys_it():
    c, ops = make(), FakeOps()
    ops.progress_until_ready = 10_000
    go(c, ops, ready_timeout_sec=0)
    ws1 = c["configs"][0]["workspace"]
    assert c["configs"][0]["status"] == "failed" and ("destroy", ws1) in kinds(ops)
    assert "使える状態になりませんでした" in str(c["configs"][0]["error"])


def test_cancel_destroys_the_current_config_and_skips_the_rest():
    c, ops = make(), FakeOps()
    ws1, ws2 = (cfg["workspace"] for cfg in c["configs"])
    ops.progress_until_ready = 3

    def cancel(workspace):
        c["cancel_requested"] = True

    ops.on_progress = cancel
    go(c, ops)
    assert kinds(ops) == [("launch", ws1), ("destroy", ws1)], "測らずに消し、次は起動しない"
    assert c["status"] == "canceled"
    assert [cfg["status"] for cfg in c["configs"]] == ["skipped", "skipped"]


def test_unexpected_error_does_not_leave_it_running():
    c, ops = make(), FakeOps()

    async def broken(workspace):
        raise RuntimeError("boom")

    ops.progress = broken
    go(c, ops)
    # 構成ごとに失敗として残し、削除はする
    assert c["configs"][0]["status"] == "failed" and "boom" in str(c["configs"][0]["error"])
    assert c["status"] == "partial" and c["finished_at"]


@pytest.mark.parametrize("change, key", [
    ({"model": "nope"}, "model.unknown"),
    ({"region": "Tokyo"}, "comparison.region_invalid"),
    ({"network_mode": "vpn"}, "comparison.network_invalid"),
    ({"runs": 0}, "bench.runs_range"),
    ({"idle_stop_minutes": 5}, "comparison.idle_stop_invalid"),
    ({"max_lifetime_hours": 0}, "comparison.max_lifetime_invalid"),
    ({"configs": REQUEST["configs"][:1]}, "comparison.config_count"),
    ({"configs": REQUEST["configs"] * 4}, "comparison.config_count"),
    ({"configs": [REQUEST["configs"][0]] * 2}, "comparison.duplicate_config"),
    ({"configs": [{**REQUEST["configs"][0], "instance_type": "g6.48xlarge"}, REQUEST["configs"][1]]},
     "comparison.instance_type_not_fit"),
    ({"configs": [{**REQUEST["configs"][0], "model_storage": "nfs"}, REQUEST["configs"][1]]}, "comparison.storage_invalid"),
    ({"configs": [{**REQUEST["configs"][0], "purchase_option": "reserved"}, REQUEST["configs"][1]]}, "comparison.purchase_invalid"),
    ({"network_mode": "private", "configs": [{**REQUEST["configs"][0], "model_storage": "download"}, REQUEST["configs"][1]]},
     "comparison.download_private"),
])
def test_validate_rejects(change, key):
    with pytest.raises(ComparisonError) as e:
        comparisons.validate({**REQUEST, **change}, {"qwen3:8b": {}}, FITTING)
    assert e.value.key == key


def test_validate_accepts_the_request():
    comparisons.validate(REQUEST, {"qwen3:8b": {}}, FITTING)


# API(偽の terraform と AWS)


class FakeComparisonAws(FakeBenchAws):
    """state を書いた環境ごとにインスタンスを返し、起動処理はすぐに使える状態になる"""

    def __init__(self, terraform_dir):
        super().__init__()
        self.terraform_dir = terraform_dir
        self.command_result = {**self.command_result}

    def _workspaces(self):
        d = self.terraform_dir / "fake-state"
        return [p.stem for p in d.glob("*.json")] if d.exists() else []

    def project_instances(self, region):
        found = []
        for ws in self._workspaces():
            tfvars = json.loads((self.terraform_dir / "tfvars" / f"{ws}.tfvars.json").read_text())
            found.append({"id": f"i-{ws}", "state": "running", "instance_type": tfvars["instance_type"],
                          "availability_zone": "ap-northeast-1a", "launch_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          "purchase_option": tfvars["purchase_option"], "env_id": ws})
        return found

    def bootstrap_status(self, region, env_id):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        return {"stage": "ready", "state": "ready", "ready_at": now, "updated_at": now, "stages": {"ready": now}}


@pytest.fixture
def terraform_dir(tmp_path):
    d = tmp_path / "terraform"
    (d / "fake-state").mkdir(parents=True)
    return d


@pytest.fixture
def aws(terraform_dir):
    return FakeComparisonAws(terraform_dir)


@pytest.fixture
def client(tmp_path, terraform_dir, aws, monkeypatch):
    monkeypatch.setenv("FAKE_TERRAFORM_WRITE_STATE", "1")
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    app = create_app(Runner(str(terraform_dir), str(fake)), web_dist=None, aws_info=aws,
                     benchmark_file=str(tmp_path / "results.jsonl"), comparison_poll_sec=0.05)
    with TestClient(app) as c:
        yield c


def wait_comparison(client, comparison_id, headers=None):
    for _ in range(400):
        found = client.get(f"/api/comparisons/{comparison_id}", headers=headers or {}).json()
        if found["status"] != "running":
            return found
        time.sleep(0.05)
    raise AssertionError("比較が終わりませんでした")


def test_api_runs_the_comparison_end_to_end(client, terraform_dir, aws):
    res = client.post("/api/comparisons", json=REQUEST)
    assert res.status_code == 201, res.text
    started = res.json()
    assert started["status"] == "running" and len(started["configs"]) == 2

    done = wait_comparison(client, started["id"])
    assert done["status"] == "succeeded", done
    assert [c["status"] for c in done["configs"]] == ["done", "done"]
    assert [c["result"]["instance_type"] for c in done["configs"]] == ["g4dn.xlarge", "g6.xlarge"]
    assert [c["result"]["purchase_option"] for c in done["configs"]] == ["spot", "on-demand"]
    assert [c["result"]["hourly_usd"] for c in done["configs"]] == [0.36, 1.2]
    assert all(c["destroyed"] for c in done["configs"])

    # 削除したので、state も tfvars も残っていない
    assert not list((terraform_dir / "fake-state").glob("cmp-*.json"))
    assert not list((terraform_dir / "tfvars").glob("cmp-*.tfvars.json"))
    # 起動の履歴には入れない(比べるための一時的な環境なので)
    assert not (terraform_dir / "tfvars" / "history.jsonl").exists()

    history = client.get("/api/benchmarks").json()["benchmarks"]
    assert {h["comparison_id"] for h in history} == {started["id"]}
    assert [cmd for _, _, cmd in aws.commands] == ["python3 /opt/llm-launchpad/launchpad.py bench qwen3:8b --runs 1"] * 2

    listed = client.get("/api/comparisons").json()["comparisons"]
    assert [c["id"] for c in listed] == [started["id"]]

    res = client.get(f"/api/comparisons/{started['id']}.csv")
    assert res.headers["content-type"].startswith("text/csv") and "attachment" in res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert list(rows[0]) == COMPARISON_CSV_COLUMNS
    assert [r["config_instance_type"] for r in rows] == ["g4dn.xlarge", "g6.xlarge"]
    assert [r["usd_per_million_tokens"] for r in rows] == ["2.5", "8.3333"]
    assert rows[0]["comparison_id"] == started["id"] and rows[0]["status"] == "done"


def test_api_reports_benchmark_failure_in_the_language(client, aws):
    aws.command_result = {"status": "Failed", "exit_code": 2, "stdout": "", "stderr": "boom"}
    started = client.post("/api/comparisons", json=REQUEST).json()
    done = wait_comparison(client, started["id"], headers={"Accept-Language": "en"})
    assert done["status"] == "partial"
    assert [c["status"] for c in done["configs"]] == ["failed", "failed"]
    assert done["configs"][0]["error"].startswith("The benchmark failed (Failed, exit code 2)")
    assert all(c["destroyed"] for c in done["configs"])


def test_api_rejects_invalid_requests(client):
    res = client.post("/api/comparisons", json={**REQUEST, "configs": REQUEST["configs"][:1]}, headers={"Accept-Language": "en"})
    assert res.status_code == 422 and res.json()["detail"] == "Choose 2 to 6 configurations to compare"
    assert client.post("/api/comparisons", json={**REQUEST, "model": "nope"}).status_code == 422
    assert client.get("/api/comparisons").json()["comparisons"] == []


def test_api_caps_context_length_at_the_model_maximum(client):
    started = client.post("/api/comparisons", json={**REQUEST, "context_length": 10_000_000}).json()
    assert started["context_length"] < 10_000_000
    wait_comparison(client, started["id"])


def test_api_allows_one_comparison_at_a_time_and_cancel(client, monkeypatch):
    # 計測を遅くして、動いている間に次を頼む
    aws_slow = client.app.state.aws_info
    original = aws_slow.run_command

    def slow(*args, **kwargs):
        time.sleep(0.5)
        return original(*args, **kwargs)

    monkeypatch.setattr(aws_slow, "run_command", slow)
    started = client.post("/api/comparisons", json=REQUEST).json()
    assert client.post("/api/comparisons", json=REQUEST).status_code == 409

    canceled = client.post(f"/api/comparisons/{started['id']}/cancel").json()
    assert canceled["cancel_requested"] is True
    done = wait_comparison(client, started["id"])
    assert done["status"] == "canceled"
    assert done["configs"][0]["destroyed"] is True
    assert done["configs"][-1]["status"] == "skipped" and done["configs"][-1]["destroyed"] is None


def test_api_comparison_not_found(client):
    assert client.get("/api/comparisons/nope").status_code == 404
    assert client.get("/api/comparisons/nope.csv").status_code == 404
    assert client.post("/api/comparisons/nope/cancel").status_code == 404
