"""リージョンとストレージ方式の API のテスト。AWS には接続せず、決まった値を返す偽の AwsInfo を使う"""

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.runner import Runner
from llm_launchpad_webui.storage import gp3_monthly, root_volume_size_gib, startup_sec

GP3 = {"storage_per_gb_month": 0.096, "throughput_per_mibps_month": 0.048, "iops_per_iops_month": 0.006}


class FakeAwsInfo:
    def __init__(self, fail=(), offering=("ap-northeast-1a", "ap-northeast-1c"), bucket_exists=True, volumes=()):
        self.fail = set(fail)
        self.offering = list(offering)
        self.bucket_exists = bucket_exists
        self.volumes = list(volumes)

    def _maybe_fail(self, name):
        if name in self.fail:
            raise AwsInfoError("aws.fetch_failed", what=name, error="AccessDenied")

    def regions(self):
        self._maybe_fail("regions")
        return [{"code": "ap-northeast-1", "name": "Asia Pacific (Tokyo)"}, {"code": "us-east-1", "name": "US East (N. Virginia)"}]

    def offering_azs(self, region, instance_type):
        self._maybe_fail("offering_azs")
        return self.offering

    def subnet_azs(self, region):
        self._maybe_fail("subnet_azs")
        return ["ap-northeast-1a", "ap-northeast-1c", "ap-northeast-1d"]

    def on_demand_hourly(self, region, instance_type):
        self._maybe_fail("on_demand_hourly")
        return 0.71

    def spot_hourly_by_az(self, region, instance_type):
        self._maybe_fail("spot_hourly_by_az")
        # 1d はインスタンス型を提供していない(offering にない)ので、一番安くても選ばない
        return {"ap-northeast-1a": 0.3409, "ap-northeast-1c": 0.3434, "ap-northeast-1d": 0.30}

    def spot_placement_scores(self, region, instance_type):
        self._maybe_fail("spot_placement_scores")
        # 1d はインスタンス型を提供していない(offering にない)ので出さない
        return {"ap-northeast-1a": 3, "ap-northeast-1c": 7, "ap-northeast-1d": 9}

    def gp3_prices(self, region):
        self._maybe_fail("gp3_prices")
        return GP3

    def s3_standard_per_gb_month(self, region):
        self._maybe_fail("s3_standard_per_gb_month")
        return 0.025

    def model_store(self, region):
        self._maybe_fail("model_store")
        return {"bucket": f"llm-launchpad-models-123456789012-{region}", "bucket_exists": self.bucket_exists, "volumes": self.volumes}


def make_client(tmp_path, aws):
    return TestClient(create_app(Runner(str(tmp_path)), web_dist=None, aws_info=aws))


def plan(client, **params):
    res = client.get("/api/region-plan", params={"model": "qwen3:8b", **params})
    assert res.status_code == 200, res.text
    return res.json()


def options(p):
    return {o["id"]: o for o in p["storage_options"]}


def test_regions(tmp_path):
    with make_client(tmp_path, FakeAwsInfo()) as c:
        r = c.get("/api/regions").json()
    assert r["default"] == "ap-northeast-1"
    assert r["regions"][0] == {"code": "ap-northeast-1", "name": "Asia Pacific (Tokyo)"}


def test_regions_error_is_502(tmp_path):
    with make_client(tmp_path, FakeAwsInfo(fail={"regions"})) as c:
        assert c.get("/api/regions").status_code == 502


def test_plan_prices_and_availability(tmp_path):
    with make_client(tmp_path, FakeAwsInfo()) as c:
        p = plan(c)
    assert p["instance_type"] == "g4dn.xlarge", "推奨のインスタンス型を使う"
    assert p["availability"] == {
        "available": True,
        "availability_zones": ["ap-northeast-1a", "ap-northeast-1c"],
        "launchable_zones": ["ap-northeast-1a", "ap-northeast-1c"],
    }
    assert p["prices"]["on_demand_hourly_usd"] == 0.71
    assert p["prices"]["spot_min"] == {"availability_zone": "ap-northeast-1a", "hourly_usd": 0.3409}, "提供していない AZ のスポット価格は使わない"
    assert p["warnings"] == []
    assert p["spot_placement_scores"] == {"ap-northeast-1a": 3, "ap-northeast-1c": 7}, "提供していない AZ のスコアは出さない"


def test_launchable_zones_need_subnet_and_offering(tmp_path):
    """サブネットのない AZ(1b)と、インスタンス型を提供していない AZ(1d)は選べない"""
    with make_client(tmp_path, FakeAwsInfo(offering=("ap-northeast-1b", "ap-northeast-1c", "ap-northeast-1a"))) as c:
        assert plan(c)["availability"]["launchable_zones"] == ["ap-northeast-1a", "ap-northeast-1c"]
    with make_client(tmp_path, FakeAwsInfo(fail={"subnet_azs"})) as c:
        p = plan(c)
    assert p["availability"]["launchable_zones"] is None and len(p["warnings"]) == 1, "分からなければ選ばせない"


def test_spot_placement_failure_is_warning(tmp_path):
    with make_client(tmp_path, FakeAwsInfo(fail={"spot_placement_scores"})) as c:
        p = plan(c)
    assert p["spot_placement_scores"] is None and len(p["warnings"]) == 1


def test_storage_options(tmp_path):
    with make_client(tmp_path, FakeAwsInfo()) as c:
        o = options(plan(c))
    # qwen3:8b は 5.2GB
    assert o["s3_cache"]["storage_monthly_usd"] == round(5.2 * 0.025, 4)
    # EBS ボリュームは 5.2 x 1.2 を切り上げた 7 + 10 = 17 GiB。スループット 250 MiB/s(125 を超える分に料金)
    assert o["ebs"]["volume_gib"] == 17
    assert o["ebs"]["storage_monthly_usd"] == round(17 * 0.096 + 125 * 0.048, 4)
    assert o["download"]["storage_monthly_usd"] == 0
    # ルートボリュームは S3 とダウンロードでモデル2つ分を足し、EBS では足さない
    assert (o["s3_cache"]["root_volume_gib"], o["ebs"]["root_volume_gib"]) == (96, 85)
    assert o["s3_cache"]["running_disk_hourly_usd"] == round(gp3_monthly(GP3, 96, 1000, 4000) / 730, 4)
    # S3 にあると取得が速い。EBS にあると取得はいらないが、読み込みは遅い
    assert o["s3_cache"]["startup_cached_sec"] < o["s3_cache"]["startup_first_sec"]
    assert o["download"]["startup_cached_sec"] is None
    assert all(opt["ready"] for opt in o.values() if opt["id"] != "ebs")
    assert o["ebs"]["prerequisite"] == "volume", "EBS ボリュームがなければ、model-store で作る必要がある"


def test_plan_without_model_store(tmp_path):
    with make_client(tmp_path, FakeAwsInfo(bucket_exists=False)) as c:
        o = options(plan(c, region="us-east-1"))
    assert o["s3_cache"]["ready"] is False and o["s3_cache"]["prerequisite"] == "bucket"
    assert o["download"]["ready"] is True


def test_type_not_offered_in_region(tmp_path):
    with make_client(tmp_path, FakeAwsInfo(offering=())) as c:
        p = plan(c, region="us-west-1")
    assert p["availability"]["available"] is False
    assert p["prices"]["spot_min"] is None


def test_price_failures_become_warnings(tmp_path):
    aws = FakeAwsInfo(fail={"on_demand_hourly", "gp3_prices", "model_store"})
    with make_client(tmp_path, aws) as c:
        p = plan(c)
    assert p["prices"]["on_demand_hourly_usd"] is None
    assert p["prices"]["spot_min"]["hourly_usd"] == 0.3409, "取れたものは出す"
    assert options(p)["ebs"]["storage_monthly_usd"] is None
    assert len(p["warnings"]) == 3


def test_context_follows_model_and_override_type(tmp_path):
    with make_client(tmp_path, FakeAwsInfo()) as c:
        assert plan(c, model="llama3.3:70b", context_length=32768)["instance_type"] == "g6.12xlarge"
        assert plan(c, instance_type="g5.xlarge")["instance_type"] == "g5.xlarge"
        # 最大を超えるコンテキスト長は、最大で見積もる(一覧と同じ)
        assert plan(c, context_length=131072)["instance_type"] == "g4dn.xlarge"
        assert c.get("/api/region-plan", params={"model": "no-such:1b"}).status_code == 422


def test_startup_matches_measurements():
    """実機の測定(qwen3:8b、5.2GB)。apply から ready まで: download 約9分半、S3 にある 約9分、EBS の2回目 約9分"""
    assert 540 <= startup_sec(5.2, "download", cached=False) <= 600
    assert 520 <= startup_sec(5.2, "s3_cache", cached=True) <= 580
    assert 520 <= startup_sec(5.2, "ebs", cached=True) <= 580
    # 大きいモデルほど長い
    assert startup_sec(87, "download", cached=False) > startup_sec(5.2, "download", cached=False) + 500
    assert root_volume_size_gib(87, True) == 259
