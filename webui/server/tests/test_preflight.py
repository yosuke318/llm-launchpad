"""起動前の事前チェックのテスト。AWS には接続せず、決まった値を返す偽の AwsInfo を使う"""

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.preflight import run_preflight
from llm_launchpad_webui.runner import Runner


class FakeAwsInfo:
    def __init__(self, fail=(), offering=("ap-northeast-1a",), quotas=None, in_use=None, vcpus=4, pending=()):
        self.fail = set(fail)
        self.offering = list(offering)
        self.quotas = quotas or {"L-DB2E81BA": 8.0, "L-3819A6DF": 8.0}
        self.in_use = in_use or {"on_demand": 0, "spot": 0}
        self.vcpus = vcpus
        self.pending = list(pending)

    def _maybe_fail(self, name):
        if name in self.fail:
            raise AwsInfoError(f"{name} を取れませんでした: AccessDenied")

    def caller_identity(self):
        self._maybe_fail("caller_identity")
        return {"account": "123456789012", "arn": "arn:aws:iam::123456789012:user/me", "profile": "default"}

    def offering_azs(self, region, instance_type):
        self._maybe_fail("offering_azs")
        return self.offering

    def instance_vcpus(self, region, instance_type):
        self._maybe_fail("instance_vcpus")
        return self.vcpus

    def service_quota(self, region, code):
        self._maybe_fail("service_quota")
        return self.quotas[code]

    def open_quota_requests(self, region, code):
        self._maybe_fail("open_quota_requests")
        return self.pending

    def g_and_vt_vcpus_in_use(self, region):
        self._maybe_fail("g_and_vt_vcpus_in_use")
        return self.in_use


def by_id(result):
    return {c["id"]: c for c in result["checks"]}


def test_all_ok():
    r = run_preflight(FakeAwsInfo(), "ap-northeast-1", "g4dn.xlarge")
    c = by_id(r)
    assert [c[i]["status"] for i in ("credentials", "instance_type", "quota_on_demand", "quota_spot")] == ["ok"] * 4
    assert c["credentials"]["account"] == "123456789012" and c["credentials"]["profile"] == "default"
    assert c["quota_spot"]["available_vcpus"] == 8 and c["quota_spot"]["needed_vcpus"] == 4
    assert r["can_proceed"] == {"spot": True, "on_demand": True}


def test_quota_not_enough_shows_request_link_and_pending_request():
    pending = [{"desired_value": 48.0, "status": "CASE_OPENED", "created": "2026-10-24T01:03:36+09:00"}]
    r = run_preflight(FakeAwsInfo(vcpus=48, pending=pending), "ap-northeast-1", "g6e.12xlarge")
    spot = by_id(r)["quota_spot"]
    assert spot["status"] == "ng"
    assert spot["request_url"] == "https://ap-northeast-1.console.aws.amazon.com/servicequotas/home/services/ec2/quotas/L-3819A6DF"
    assert spot["pending_requests"] == pending, "申請中のものがあれば出す"
    assert r["can_proceed"] == {"spot": False, "on_demand": False}


def test_running_instances_count_against_quota():
    """動いている G 系のインスタンスの分は使えない。スポットとオンデマンドは別枠"""
    r = run_preflight(FakeAwsInfo(in_use={"on_demand": 0, "spot": 8}), "ap-northeast-1", "g4dn.xlarge")
    assert by_id(r)["quota_spot"]["status"] == "ng" and by_id(r)["quota_spot"]["available_vcpus"] == 0
    assert r["can_proceed"] == {"spot": False, "on_demand": True}


def test_type_not_offered_blocks_both():
    r = run_preflight(FakeAwsInfo(offering=()), "us-west-1", "g6e.xlarge")
    assert by_id(r)["instance_type"]["status"] == "ng"
    assert r["can_proceed"] == {"spot": False, "on_demand": False}


def test_no_credentials_stops_other_checks():
    r = run_preflight(FakeAwsInfo(fail={"caller_identity"}), "ap-northeast-1", "g4dn.xlarge")
    assert [c["id"] for c in r["checks"]] == ["credentials"]
    assert r["checks"][0]["status"] == "ng" and "AccessDenied" in r["checks"][0]["error"]
    assert r["can_proceed"] == {"spot": False, "on_demand": False}


def test_unknown_does_not_block():
    """Service Quotas を見る権限がないなどで確かめられなくても、進むのは止めない"""
    r = run_preflight(FakeAwsInfo(fail={"service_quota"}), "ap-northeast-1", "g4dn.xlarge")
    assert by_id(r)["quota_spot"]["status"] == "unknown"
    assert r["can_proceed"] == {"spot": True, "on_demand": True}


def test_vcpus_fall_back_to_catalog():
    r = run_preflight(FakeAwsInfo(fail={"instance_vcpus"}), "ap-northeast-1", "g4dn.xlarge", catalog_vcpus=4)
    assert by_id(r)["quota_spot"]["needed_vcpus"] == 4 and by_id(r)["quota_spot"]["status"] == "ok"


def test_usage_unknown_is_unknown():
    r = run_preflight(FakeAwsInfo(fail={"g_and_vt_vcpus_in_use"}), "ap-northeast-1", "g4dn.xlarge")
    assert by_id(r)["quota_on_demand"]["status"] == "unknown"
    assert "g_and_vt_vcpus_in_use" in by_id(r)["quota_on_demand"]["error"]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Runner(str(tmp_path)), web_dist=None, aws_info=FakeAwsInfo(vcpus=48))) as c:
        yield c


def test_api(client):
    r = client.get("/api/preflight", params={"instance_type": "g6e.12xlarge", "region": "ap-northeast-1"}).json()
    assert r["can_proceed"] == {"spot": False, "on_demand": False}
    assert by_id(r)["quota_on_demand"]["limit_vcpus"] == 8.0
