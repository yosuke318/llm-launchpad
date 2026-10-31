"""Bedrock の環境(/api/bedrock/...)のテスト。terraform は偽物(fake_terraform.py)、AWS も偽物を使う"""

import json
import os
import stat
import sys
import time

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui import bedrock
from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.runner import Runner

from test_app import events

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "..", "catalog"))

HAIKU = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"


class FakeAwsInfo:
    def __init__(self, fail_prices=False, fail_invoke=False):
        self.fail_prices = fail_prices
        self.fail_invoke = fail_invoke
        self.invoked = []

    def bedrock_token_prices(self, region, price_model):
        if self.fail_prices:
            raise AwsInfoError("aws.fetch_failed", what="Bedrock の料金", error="denied")
        return {"Claude Haiku 4.5": {"input": 1.1, "output": 5.5}, "Claude Sonnet 4.6": {"input": 3.3, "output": 16.5}}[price_model]

    def interface_endpoint_hourly(self, region):
        return 0.014

    def invoke_lambda(self, region, function_name, payload, timeout_sec=900):
        self.invoked.append((region, function_name, payload))
        if self.fail_invoke:
            raise AwsInfoError("aws.lambda_failed", function=function_name, error="not available for this account")
        if payload["action"] == "chat":
            return {"model": HAIKU, "content": "東京", "usage": {"inputTokens": 18, "outputTokens": 6}}
        return {
            "model": HAIKU, "measured_at": "2026-10-31T01:30:45+00:00", "num_predict": 256, "runs": payload["runs"],
            "ttft_sec": 0.534, "output_tokens_per_sec": 163.96, "prompt_tokens": 79, "output_tokens": 606,
            "results": [{"prompt_id": "short", "run": 1}], "endpoint": {"host": "bedrock-runtime.ap-northeast-1.amazonaws.com", "addresses": ["10.43.0.21"]},
        }


@pytest.fixture
def dirs(tmp_path):
    main, br = tmp_path / "terraform", tmp_path / "terraform" / "bedrock"
    br.mkdir(parents=True)
    return main, br


def make_client(tmp_path, dirs, aws):
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    main, br = dirs
    app = create_app(
        Runner(str(main), str(fake)), web_dist=None, catalog_dir=CATALOG_DIR, aws_info=aws,
        benchmark_file=str(tmp_path / "results.jsonl"),
        bedrock_runner=Runner(str(br), str(fake), launch_variables=bedrock.LAUNCH_VARIABLES),
    )
    return TestClient(app)


def launch(client, workspace="br-haiku", variables=None):
    return client.post("/api/bedrock/launch", json={"workspace": workspace, "variables": variables or {"model": HAIKU, "region": "ap-northeast-1"}})


def test_models_with_token_prices_and_endpoint_hourly(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        body = client.get("/api/bedrock/models").json()
    assert body["default"] == HAIKU
    haiku = next(m for m in body["models"] if m["id"] == HAIKU)
    assert (haiku["input_usd_per_million"], haiku["output_usd_per_million"]) == (1.1, 5.5)
    assert body["endpoint_hourly_usd"] == 0.014, "エンドポイントは bedrock-runtime の1つだけ"
    assert body["warnings"] == []


def test_models_keep_going_without_prices(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo(fail_prices=True)) as client:
        body = client.get("/api/bedrock/models").json()
    assert all(m["input_usd_per_million"] is None for m in body["models"])
    assert body["warnings"], "料金が取れなかった理由を残す"


def test_models_only_in_the_region(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        assert client.get("/api/bedrock/models", params={"region": "us-east-1"}).json()["models"] == []


def test_launch_runs_in_bedrock_dir_with_its_own_variables(tmp_path, dirs):
    _, br = dirs
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        res = launch(client)
        assert res.status_code == 201, res.text
        run = res.json()
        assert [s[0] for s in run["steps"]] == ["init", "apply"]
        # 起動の出力は、GPUインスタンスの環境と同じ /api/runs/{id}/events で読める
        assert events(client, run["id"])[-1] == ("end", {"status": "succeeded", "exit_code": 0})
        assert json.loads((br / "tfvars" / "br-haiku.tfvars.json").read_text()) == {"model": HAIKU, "region": "ap-northeast-1"}

        rejected = launch(client, variables={"model": HAIKU, "model_storage": "ebs"})
        assert rejected.status_code == 422, "GPUインスタンスの変数は書かない"


def test_launch_rejects_model_not_in_catalog(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        assert launch(client, variables={"model": "qwen3:8b"}).status_code == 422


def test_environments_chat_and_destroy(tmp_path, dirs):
    _, br = dirs
    aws = FakeAwsInfo()
    with make_client(tmp_path, dirs, aws) as client:
        events(client, launch(client).json()["id"])
        envs = client.get("/api/bedrock/environments").json()["environments"]
        assert [(e["workspace"], e["model_name"], e["function_name"]) for e in envs] == [("br-haiku", "Claude Haiku 4.5", "llm-launchpad-br-haiku-bedrock")]

        res = client.post("/api/bedrock/environments/br-haiku/chat", json={"messages": [{"role": "user", "content": "日本の首都は？"}]})
        assert res.status_code == 200, res.text
        assert res.json()["content"] == "東京"
        assert aws.invoked[-1] == ("ap-northeast-1", "llm-launchpad-br-haiku-bedrock", {"action": "chat", "messages": [{"role": "user", "content": "日本の首都は？"}]})

        run = client.post("/api/bedrock/environments/br-haiku/destroy").json()
        assert run["command"] == "destroy"
        events(client, run["id"])
        assert not (br / "tfvars" / "br-haiku.tfvars.json").exists(), "消した環境は一覧に出さない"
        assert client.get("/api/bedrock/environments").json()["environments"] == []


def test_chat_before_created_is_404(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        assert client.post("/api/bedrock/environments/nothing/chat", json={"messages": [{"role": "user", "content": "x"}]}).status_code == 404


def test_chat_failure_is_502(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo(fail_invoke=True)) as client:
        events(client, launch(client).json()["id"])
        res = client.post("/api/bedrock/environments/br-haiku/chat", json={"messages": [{"role": "user", "content": "x"}]})
    assert res.status_code == 502
    assert "not available for this account" in res.json()["detail"]


def wait_job(client, job_id):
    for _ in range(100):
        job = client.get(f"/api/benchmarks/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("計測が終わらない")


def test_benchmark_saves_record_with_token_cost(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        events(client, launch(client).json()["id"])
        res = client.post("/api/bedrock/environments/br-haiku/benchmark", json={"runs": 2})
        assert res.status_code == 201, res.text
        job = wait_job(client, res.json()["id"])
        assert job["status"] == "succeeded", job
        record = client.get("/api/benchmarks").json()["benchmarks"][0]
        csv_text = client.get("/api/benchmarks.csv").text
    assert record["engine"] == "bedrock" and record["instance_type"] is None and record["runs"] == 2
    assert record["ttft_sec"] == 0.534 and record["output_tokens_per_sec"] == 163.96
    # (79 × 1.1 + 606 × 5.5) / 100万 を 606 トークンで出したので、100万トークンあたり 5.6434 USD
    assert record["usd_per_million_tokens"] == 5.6434
    assert csv_text.splitlines()[0].startswith("id,engine,"), "CSV にもエンジンの列を出す"


def test_usd_per_million_output_tokens():
    assert bedrock.usd_per_million_output_tokens({"input": 1.0, "output": 5.0}, 0, 100) == 5.0
    assert bedrock.usd_per_million_output_tokens({"input": 1.0, "output": 5.0}, 100, 100) == 6.0
    assert bedrock.usd_per_million_output_tokens({"input": None, "output": 5.0}, 1, 1) is None
    assert bedrock.usd_per_million_output_tokens({"input": 1.0, "output": 5.0}, 1, 0) is None


def test_catalog_engines_include_bedrock(tmp_path, dirs):
    with make_client(tmp_path, dirs, FakeAwsInfo()) as client:
        engines = client.get("/api/catalog").json()["engines"]
    assert {"id": "bedrock", "available": True} in engines
