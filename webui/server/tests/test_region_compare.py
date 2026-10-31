"""リージョンの比較とおすすめ(/api/region-compare)のテスト。AWS には接続しない"""

from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.aws_info import AwsInfoError
from llm_launchpad_webui.region_compare import compare
from llm_launchpad_webui.runner import Runner

REGIONS = [
    {"code": "ap-northeast-1", "name": "Asia Pacific (Tokyo)"},
    {"code": "eu-central-1", "name": "Europe (Frankfurt)"},
    {"code": "eu-west-1", "name": "Europe (Ireland)"},
    {"code": "us-east-1", "name": "US East (N. Virginia)"},
    {"code": "us-east-2", "name": "US East (Ohio)"},
]
ON_DEMAND = {"ap-northeast-1": 1.1672, "eu-central-1": 1.0064, "us-east-1": 0.8048, "us-east-2": 0.8048}
SPOT = {
    "ap-northeast-1": {"availability_zone": "ap-northeast-1c", "hourly_usd": 0.42},
    "eu-central-1": {"availability_zone": "eu-central-1b", "hourly_usd": 0.38},
    "us-east-1": {"availability_zone": "us-east-1d", "hourly_usd": 0.30},
}
SCORES = {"ap-northeast-1": 1, "eu-central-1": 9, "us-east-1": 3, "us-east-2": 2}


def test_rows_only_offered_regions_sorted_by_spot():
    res = compare(REGIONS, ON_DEMAND, SPOT, SCORES, current="ap-northeast-1")
    assert [r["code"] for r in res["regions"]] == ["us-east-1", "eu-central-1", "ap-northeast-1", "us-east-2"], (
        "スポットの安い順、スポットのないところは後ろ。提供していない eu-west-1 は出さない"
    )
    assert [r["code"] for r in res["regions"] if r["current"]] == ["ap-northeast-1"]


def test_recommend_cheap_and_low_risk_spot():
    res = compare(REGIONS, ON_DEMAND, SPOT, SCORES)
    # us-east-1 が一番安いが score 3 で中断されやすいので、score 9 の eu-central-1
    assert res["recommended"] == {"spot": "eu-central-1", "on_demand": "us-east-1"}, "オンデマンドが同じ値なら名前順"


def test_no_spot_recommendation_without_low_risk_region():
    res = compare(REGIONS, ON_DEMAND, SPOT, {"us-east-1": 6})
    assert res["recommended"]["spot"] is None


class FakeAwsInfo:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.spot_regions = None

    def regions(self):
        if "regions" in self.fail:
            raise AwsInfoError("aws.fetch_failed", what="regions", error="denied")
        return REGIONS

    def on_demand_hourly_by_region(self, instance_type):
        if "on_demand" in self.fail:
            raise AwsInfoError("aws.fetch_failed", what="on_demand", error="denied")
        return ON_DEMAND

    def spot_min_by_region(self, regions, instance_type):
        self.spot_regions = list(regions)
        return {k: v for k, v in SPOT.items() if k in regions}, []

    def region_placement_scores(self, regions, instance_type):
        if "scores" in self.fail:
            raise AwsInfoError("aws.fetch_failed", what="scores", error="MaxConfigLimitExceeded")
        return SCORES


def get(tmp_path, aws, **params):
    with TestClient(create_app(Runner(str(tmp_path)), web_dist=None, aws_info=aws)) as c:
        return c.get("/api/region-compare", params={"instance_type": "g6.xlarge", **params})


def test_api_compares_offered_regions(tmp_path):
    aws = FakeAwsInfo()
    res = get(tmp_path, aws, region="us-east-2")
    assert res.status_code == 200, res.text
    body = res.json()
    assert aws.spot_regions == ["ap-northeast-1", "eu-central-1", "us-east-1", "us-east-2"], "提供しているリージョンだけスポットを問い合わせる"
    assert body["recommended"] == {"spot": "eu-central-1", "on_demand": "us-east-1"}
    assert [r["code"] for r in body["regions"] if r["current"]] == ["us-east-2"]
    assert body["warnings"] == [] and body["low_risk_score"] == 7


def test_api_keeps_going_without_scores(tmp_path):
    body = get(tmp_path, FakeAwsInfo(fail={"scores"})).json()
    assert body["recommended"]["spot"] is None and body["recommended"]["on_demand"] == "us-east-1"
    assert body["warnings"], "起動しやすさを取れなかった理由を残す"


def test_api_without_on_demand_asks_all_regions(tmp_path):
    aws = FakeAwsInfo(fail={"on_demand"})
    body = get(tmp_path, aws).json()
    assert len(aws.spot_regions) == len(REGIONS)
    assert [r["code"] for r in body["regions"]] == ["us-east-1", "eu-central-1", "ap-northeast-1"]


def test_api_regions_failure_is_502(tmp_path):
    assert get(tmp_path, FakeAwsInfo(fail={"regions"})).status_code == 502
