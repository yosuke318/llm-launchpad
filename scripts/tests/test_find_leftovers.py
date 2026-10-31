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


def without_leftovers(res):
    """state にない2つ(vol-old と sir-orphan)を除いた応答"""
    res[("ec2", "describe-volumes", REGION)] = {"Volumes": [v for v in res[("ec2", "describe-volumes", REGION)]["Volumes"] if v["VolumeId"] != "vol-old"]}
    res[("ec2", "describe-spot-instance-requests", REGION)]["SpotInstanceRequests"].pop(1)
    return res


def fail(message):
    """呼ばれると AwsError を出す応答"""
    def raise_error(args):
        raise fl.AwsError(message)
    return raise_error


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

    def test_finds_interface_endpoints(self):
        res = responses()
        res[("ec2", "describe-vpc-endpoints", REGION)] = {"VpcEndpoints": [
            {"VpcEndpointId": "vpce-ssm", "VpcEndpointType": "Interface", "ServiceName": "com.amazonaws.ap-northeast-1.ssm",
             "State": "available", "SubnetIds": ["subnet-a"], "Tags": tag(Project="llm-launchpad", EnvId="old-env")},
        ]}
        code, found = self.resources(FakeAws(res))
        self.assertEqual(code, 2)
        self.assertFalse(found["vpce-ssm"]["in_state"])
        self.assertEqual(found["vpce-ssm"]["note"], "1つのAZに置いた分の時間課金がある")

    def test_finds_bedrock_lambda_and_log_group(self):
        res = responses()
        res[("lambda", "list-functions", REGION)] = {"Functions": [
            {"FunctionName": "llm-launchpad-br-haiku-bedrock", "FunctionArn": "arn:aws:lambda:ap-northeast-1:123:function:llm-launchpad-br-haiku-bedrock", "Runtime": "python3.13"},
            {"FunctionName": "someone-else", "FunctionArn": "arn:aws:lambda:ap-northeast-1:123:function:someone-else"},
        ]}
        res[("logs", "describe-log-groups", REGION)] = {"logGroups": [
            {"logGroupName": "/aws/lambda/llm-launchpad-br-haiku-bedrock", "storedBytes": 2048},
        ]}
        code, found = self.resources(FakeAws(res))
        self.assertEqual(code, 2)
        self.assertNotIn("someone-else", found)
        fn = found["llm-launchpad-br-haiku-bedrock"]
        self.assertEqual((fn["type"], fn["env_id"], fn["in_state"]), ("lambda", "br-haiku", False))
        log = found["/aws/lambda/llm-launchpad-br-haiku-bedrock"]
        self.assertEqual((log["type"], log["env_id"]), ("log-group", "br-haiku"))

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
        code, _ = self.resources(FakeAws(without_leftovers(responses())))
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

    def test_failed_service_keeps_other_resources(self):
        res = responses()
        res[("ec2", "describe-vpcs", REGION)] = fail("aws ec2 describe-vpcs が失敗しました(ap-northeast-1): UnauthorizedOperation")
        code, out = run(FakeAws(res), self.tmp.name)
        self.assertIn("[エラー] aws ec2 describe-vpcs", out)
        self.assertIn("vol-old", out, "VPC を調べられなくても、同じリージョンのほかのリソースは出す")
        self.assertNotIn("vpc-0aaa", out)
        self.assertEqual(code, 2, "調べられなかったものがあっても、消し忘れが見つかれば終了コード 2")

    def test_failed_service_without_leftovers_exit_one(self):
        res = without_leftovers(responses())
        res[("ec2", "describe-vpcs", REGION)] = fail("aws ec2 describe-vpcs が失敗しました(ap-northeast-1): UnauthorizedOperation")
        code, _ = run(FakeAws(res), self.tmp.name)
        self.assertEqual(code, 1, "消し忘れが見つからなくても、調べられなかったものがあれば終了コード 1")

    def test_price_failure_does_not_hide_resources(self):
        res = responses()
        res[("ec2", "describe-spot-price-history", REGION)] = fail("aws ec2 describe-spot-price-history が失敗しました: AccessDenied")
        res[("pricing", "get-products", "us-east-1")] = fail("aws pricing get-products が失敗しました: ThrottlingException")
        code, out = run(FakeAws(res), self.tmp.name, "--json")
        data = json.loads(out)
        found = {r["id"]: r for r in data["resources"]}
        self.assertIn("i-0aaa", found, "料金を取れなくても、インスタンスは出す")
        self.assertIn("vol-old", found, "料金を取れなくても、消し忘れのボリュームは出す")
        self.assertIsNone(found["i-0aaa"]["hourly_usd"])
        self.assertIsNone(found["vol-old"]["monthly_usd"])
        self.assertEqual(data["errors"], [], "料金の失敗はエラーにしない")
        self.assertTrue(any("料金を取れませんでした" in w for w in data["warnings"]))
        self.assertEqual(code, 2)

    def test_bucket_size_failure_keeps_bucket(self):
        res = responses()
        res[("cloudwatch", "get-metric-statistics", REGION)] = fail("aws cloudwatch get-metric-statistics が失敗しました: AccessDenied")
        code, out = run(FakeAws(res), self.tmp.name)
        self.assertIn("s3-bucket      llm-launchpad-models-123-ap-northeast-1", out)
        self.assertIn("[注意] バケット llm-launchpad-models-123-ap-northeast-1 の大きさを取れませんでした", out)
        self.assertEqual(code, 2)

    def test_nested_ids_in_state(self):
        ids = set(fl.strings_in({"a": [{"b": "vol-x"}], "c": "i-y", "n": 3}))
        self.assertEqual(ids, {"vol-x", "i-y"})


if __name__ == "__main__":
    unittest.main()
