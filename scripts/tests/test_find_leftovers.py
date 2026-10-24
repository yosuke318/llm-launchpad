"""find_leftovers.py のテスト。AWS には接続せず、決まった応答を返す偽の AWS CLI を使う

    python3 -B -m unittest discover -s scripts/tests
"""

import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import find_leftovers as fl  # noqa: E402

NOW = datetime.datetime(2026, 10, 24, 12, 0, tzinfo=datetime.timezone.utc)
REGION = "ap-northeast-1"


def price_list(usd, begin_range=None):
    dim = {"pricePerUnit": {"USD": str(usd)}}
    if begin_range is not None:
        dim["beginRange"] = begin_range
    return {"PriceList": [json.dumps({"terms": {"OnDemand": {"t": {"priceDimensions": {"d": dim}}}}})]}


class FakeAws:
    """(サービス, 操作, リージョン) ごとに決まった応答を返す。呼ばれた操作を覚えておく"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, service, operation, *args, region=None, ok_errors=()):
        self.calls.append((service, operation, region, args))
        key = (service, operation, region)
        if key not in self.responses:
            key = (service, operation, None)
        res = self.responses.get(key, {})
        return res(args) if callable(res) else res


def tag(**kv):
    return [{"Key": k, "Value": v} for k, v in kv.items()]


ENV_TAGS = tag(Project="llm-launchpad", EnvId="qwen-test", Name="llm-launchpad-qwen-test")
STORE_TAGS = tag(Project="llm-launchpad", EnvId="model-store", Persistent="true")


def responses():
    return {
        ("ec2", "describe-regions", "us-east-1"): {"Regions": [{"RegionName": REGION}]},
        ("ec2", "describe-instances", REGION): {"Reservations": [{"Instances": [{
            "InstanceId": "i-0aaa",
            "InstanceType": "g4dn.xlarge",
            "InstanceLifecycle": "spot",
            "SpotInstanceRequestId": "sir-live",
            "State": {"Name": "running"},
            "LaunchTime": "2026-10-24T10:00:00+00:00",
            "Placement": {"AvailabilityZone": "ap-northeast-1a"},
            "Tags": ENV_TAGS,
        }]}]},
        ("ec2", "describe-spot-price-history", REGION): {"SpotPriceHistory": [{"SpotPrice": "0.340000"}]},
        ("ec2", "describe-volumes", REGION): {"Volumes": [
            # インスタンスのルートボリューム(state では入れ子の中にIDがある)
            {"VolumeId": "vol-root", "VolumeType": "gp3", "Size": 96, "State": "in-use",
             "CreateTime": "2026-10-24T10:00:00+00:00", "Attachments": [{"InstanceId": "i-0aaa"}], "Tags": ENV_TAGS},
            # state にないボリューム(消し忘れ)
            {"VolumeId": "vol-old", "VolumeType": "gp3", "Size": 100, "State": "available",
             "CreateTime": "2026-10-14T12:00:00+00:00", "Attachments": [], "Tags": tag(Project="llm-launchpad", EnvId="old-env")},
            # model-store のボリューム(残す設定)
            {"VolumeId": "vol-model", "VolumeType": "gp3", "Size": 200, "State": "available",
             "CreateTime": "2026-10-01T00:00:00+00:00", "Attachments": [], "Tags": STORE_TAGS},
        ]},
        ("pricing", "get-products", "us-east-1"): lambda args: price_list(0.096) if "AmazonEC2" in args else price_list(0.025, "0"),
        ("ec2", "describe-spot-instance-requests", REGION): {"SpotInstanceRequests": [
            {"SpotInstanceRequestId": "sir-live", "State": "active", "Type": "persistent", "InstanceId": "i-0aaa",
             "LaunchSpecification": {"IamInstanceProfile": {"Name": "llm-lp-qwen-test-1"}}},
            # インスタンスはないのにリクエストだけ残っている
            {"SpotInstanceRequestId": "sir-orphan", "State": "disabled", "Type": "persistent",
             "LaunchSpecification": {"IamInstanceProfile": {"Arn": "arn:aws:iam::123:instance-profile/llm-lp-old-env-9"}}},
            # ほかのプロジェクトのリクエスト
            {"SpotInstanceRequestId": "sir-other", "State": "active", "Type": "one-time", "InstanceId": "i-other",
             "LaunchSpecification": {"IamInstanceProfile": {"Name": "someone-else"}}},
        ]},
        ("ec2", "describe-vpcs", REGION): {"Vpcs": [{"VpcId": "vpc-0aaa", "CidrBlock": "10.42.0.0/16", "Tags": ENV_TAGS}]},
        ("ssm", "describe-parameters", REGION): {"Parameters": [{"Name": "/llm-launchpad/qwen-test/status", "Tier": "Standard"}]},
        ("scheduler", "list-schedules", REGION): {"Schedules": [{"Name": "llm-lp-qwen-test-ttl-20261024", "State": "ENABLED"}]},
        ("s3api", "list-buckets", None): {"Buckets": [{"Name": "llm-launchpad-models-123-ap-northeast-1"}, {"Name": "unrelated-bucket"}]},
        ("s3api", "get-bucket-tagging", None): lambda args: {"TagSet": STORE_TAGS} if "llm-launchpad-models-123-ap-northeast-1" in args else None,
        ("s3api", "get-bucket-location", None): {"LocationConstraint": REGION},
        ("cloudwatch", "get-metric-statistics", REGION): {"Datapoints": [{"Timestamp": "2026-10-23T00:00:00Z", "Average": 50e9}]},
        ("iam", "list-roles", None): {"Roles": [
            {"RoleName": "llm-lp-qwen-test-abc", "Arn": "arn:aws:iam::123:role/llm-lp-qwen-test-abc"},
            {"RoleName": "OtherRole", "Arn": "arn:aws:iam::123:role/OtherRole"},
        ]},
    }


def write_state(dir_path, resources):
    path = os.path.join(dir_path, "terraform.tfstate.d", "qwen-test", "terraform.tfstate")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        json.dump({"resources": resources}, f)


MANAGED_STATE = [
    {"instances": [{"attributes": {"id": "i-0aaa", "spot_instance_request_id": "sir-live",
                                   "root_block_device": [{"volume_id": "vol-root"}]}}]},
    {"instances": [{"attributes": {"id": "vpc-0aaa"}}]},
    {"instances": [{"attributes": {"id": "/llm-launchpad/qwen-test/status", "name": "/llm-launchpad/qwen-test/status"}}]},
    {"instances": [{"attributes": {"id": "default/llm-lp-qwen-test-ttl-20261024", "name": "llm-lp-qwen-test-ttl-20261024"}}]},
    {"instances": [{"attributes": {"id": "llm-lp-qwen-test-abc", "name": "llm-lp-qwen-test-abc"}}]},
]


def run(fake, state_dir, *extra):
    out = io.StringIO()
    with redirect_stdout(out):
        code = fl.main(["--state-dir", state_dir, *extra], aws=fake, now=NOW)
    return code, out.getvalue()


class FindLeftoversTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        write_state(self.tmp.name, MANAGED_STATE)

    def resources(self, fake=None):
        code, out = run(fake or FakeAws(responses()), self.tmp.name, "--json")
        data = json.loads(out)
        return code, {r["id"]: r for r in data["resources"]}

    def test_finds_resources_and_marks_state(self):
        code, found = self.resources()
        self.assertEqual(code, 2, "state にないリソースがあれば終了コード 2")
        for rid in ["i-0aaa", "vol-root", "vpc-0aaa", "/llm-launchpad/qwen-test/status", "llm-lp-qwen-test-ttl-20261024", "llm-lp-qwen-test-abc", "sir-live"]:
            self.assertTrue(found[rid]["in_state"], f"{rid} は state にある")
        self.assertFalse(found["vol-old"]["in_state"])
        self.assertFalse(found["sir-orphan"]["in_state"], "インスタンスのないスポットリクエストも見つける")
        self.assertNotIn("sir-other", found, "ほかのプロジェクトのスポットリクエストは数えない")
        self.assertNotIn("unrelated-bucket", found)
        self.assertNotIn("OtherRole", found)

    def test_persistent_resources_are_not_leftovers(self):
        _, found = self.resources()
        self.assertTrue(found["vol-model"]["persistent"])
        self.assertTrue(found["llm-launchpad-models-123-ap-northeast-1"]["persistent"])

    def test_costs(self):
        _, found = self.resources()
        inst = found["i-0aaa"]
        self.assertEqual((inst["hours"], inst["hourly_usd"], inst["cost_so_far_usd"]), (2.0, 0.34, 0.68), "スポット価格 x 今回の起動からの時間")
        old = found["vol-old"]
        # 100 GiB x $0.096 = $9.6/月。10日(240時間)分は 9.6 x 240 / 730
        self.assertEqual((old["monthly_usd"], old["cost_so_far_usd"]), (9.6, round(9.6 * 240 / 730, 2)))
        bucket = found["llm-launchpad-models-123-ap-northeast-1"]
        self.assertEqual((bucket["region"], bucket["monthly_usd"]), (REGION, 1.25), "50GB x $0.025")

    def test_no_leftovers_exit_zero(self):
        res = responses()
        res[("ec2", "describe-volumes", REGION)] = {"Volumes": [v for v in res[("ec2", "describe-volumes", REGION)]["Volumes"] if v["VolumeId"] != "vol-old"]}
        res[("ec2", "describe-spot-instance-requests", REGION)]["SpotInstanceRequests"].pop(1)
        code, _ = self.resources(FakeAws(res))
        self.assertEqual(code, 0, "state にないのが残す設定のものだけなら終了コード 0")

    def test_stopped_instance_has_no_hourly_cost(self):
        res = responses()
        inst = res[("ec2", "describe-instances", REGION)]["Reservations"][0]["Instances"][0]
        inst["State"] = {"Name": "stopped"}
        _, found = self.resources(FakeAws(res))
        self.assertIsNone(found["i-0aaa"]["hourly_usd"])

    def test_text_report_groups_by_region(self):
        code, out = run(FakeAws(responses()), self.tmp.name, "--regions", REGION)
        self.assertEqual(code, 2)
        self.assertIn(f"== {REGION}", out)
        self.assertIn("== global", out)
        self.assertIn("!! ebs-volume     vol-old env=old-env  [state にない]", out)
        self.assertIn("state にない(残す設定のものを除く): 2 件", out)

    def test_regions_option_skips_region_lookup(self):
        fake = FakeAws(responses())
        run(fake, self.tmp.name, "--regions", REGION)
        self.assertNotIn(("ec2", "describe-regions"), [(c[0], c[1]) for c in fake.calls])

    def test_aws_error_is_reported(self):
        res = responses()

        def fail(args):
            raise fl.AwsError("aws ec2 describe-vpcs が失敗しました(ap-northeast-1): UnauthorizedOperation")

        res[("ec2", "describe-vpcs", REGION)] = fail
        code, out = run(FakeAws(res), self.tmp.name)
        self.assertEqual(code, 1, "調べられなかったリージョンがあれば終了コード 1")
        self.assertIn("[エラー] aws ec2 describe-vpcs", out)

    def test_nested_ids_in_state(self):
        ids = set(fl.strings_in({"a": [{"b": "vol-x"}], "c": "i-y", "n": 3}))
        self.assertEqual(ids, {"vol-x", "i-y"})


if __name__ == "__main__":
    unittest.main()
