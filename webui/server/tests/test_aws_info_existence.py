"""消し忘れの候補が今もあるかを、種類ごとの API で確かめるテスト。AWS には接続せず、決まった応答を返す偽物を使う"""

import pytest
from botocore.exceptions import ClientError

from llm_launchpad_webui.aws_info import AwsInfo, AwsInfoError

REGION = "ap-northeast-1"
ACCOUNT = "123456789012"


def ec2_arn(kind, rid):
    return f"arn:aws:ec2:{REGION}:{ACCOUNT}:{kind}/{rid}"


class FakePaginator:
    def __init__(self, client, method):
        self.client = client
        self.method = method

    def paginate(self, **kwargs):
        self.client.calls.append((self.method, kwargs))
        if self.client.error:
            raise self.client.error
        values = kwargs["Filters"][0]["Values"]
        list_key, id_key = self.client.keys[self.method]
        # ID で絞ったときと同じく、あるものだけを返す
        yield {list_key: [item for item in self.client.items if item[id_key] in values]}


class FakeEc2:
    keys = {
        "describe_volumes": ("Volumes", "VolumeId"),
        "describe_vpcs": ("Vpcs", "VpcId"),
        "describe_vpc_endpoints": ("VpcEndpoints", "VpcEndpointId"),
        "describe_security_group_rules": ("SecurityGroupRules", "SecurityGroupRuleId"),
    }

    def __init__(self, items=(), error=None):
        self.items = list(items)
        self.error = error
        self.calls = []

    def get_paginator(self, method):
        return FakePaginator(self, method)


class FakeSsm:
    def __init__(self, names=()):
        self.names = set(names)
        self.calls = []

    def get_parameters(self, Names):
        self.calls.append(Names)
        assert len(Names) <= 10, "get_parameters は一度に10個まで"
        return {
            "Parameters": [{"Name": n} for n in Names if n in self.names],
            "InvalidParameters": [n for n in Names if n not in self.names],
        }


class FakeSession:
    def __init__(self, **clients):
        self.clients = clients

    def client(self, service, region_name=None):
        return self.clients[service]


def test_ec2_volumes_drop_missing_and_deleting():
    ec2 = FakeEc2([
        {"VolumeId": "vol-0left", "State": "available"},
        {"VolumeId": "vol-0deleting", "State": "deleting"},
    ])
    arns = [ec2_arn("volume", v) for v in ("vol-0left", "vol-0deleting", "vol-0gone")]
    found = AwsInfo(session=FakeSession(ec2=ec2)).existing_arns(REGION, "ec2:volume", arns)
    assert found == {ec2_arn("volume", "vol-0left")}, "消している途中のものと、もうないものは除く"
    assert ec2.calls == [("describe_volumes", {"Filters": [{"Name": "volume-id", "Values": ["vol-0deleting", "vol-0gone", "vol-0left"]}]})]


def test_ec2_vpc_endpoint_deleted_state_is_gone():
    ec2 = FakeEc2([{"VpcEndpointId": "vpce-0a", "State": "Deleted"}, {"VpcEndpointId": "vpce-0b", "State": "available"}])
    arns = [ec2_arn("vpc-endpoint", "vpce-0a"), ec2_arn("vpc-endpoint", "vpce-0b")]
    assert AwsInfo(session=FakeSession(ec2=ec2)).existing_arns(REGION, "ec2:vpc-endpoint", arns) == {arns[1]}


def test_ec2_security_group_rules_without_state():
    ec2 = FakeEc2([{"SecurityGroupRuleId": "sgr-0left"}])
    arns = [ec2_arn("security-group-rule", "sgr-0left"), ec2_arn("security-group-rule", "sgr-0gone")]
    assert AwsInfo(session=FakeSession(ec2=ec2)).existing_arns(REGION, "ec2:security-group-rule", arns) == {arns[0]}


def test_ec2_ids_are_split_into_chunks():
    ec2 = FakeEc2([{"VpcId": f"vpc-{i:04d}"} for i in range(250)])
    arns = [ec2_arn("vpc", f"vpc-{i:04d}") for i in range(250)]
    assert AwsInfo(session=FakeSession(ec2=ec2)).existing_arns(REGION, "ec2:vpc", arns) == set(arns)
    assert [len(kw["Filters"][0]["Values"]) for _, kw in ec2.calls] == [100, 100, 50]


def test_ssm_parameters_by_name():
    ssm = FakeSsm(names=["/llm-launchpad/left/status"])
    arns = [f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/llm-launchpad/{e}/status" for e in ["left"] + [f"gone{i}" for i in range(11)]]
    found = AwsInfo(session=FakeSession(ssm=ssm)).existing_arns(REGION, "ssm:parameter", arns)
    assert found == {arns[0]}, "ARN から先頭に / の付いた名前に直して問い合わせる"
    assert [len(c) for c in ssm.calls] == [10, 2]


@pytest.mark.parametrize("kind", ["ec2:instance", "scheduler:schedule", "iam:role"])
def test_unsupported_kinds_are_not_checked(kind):
    assert AwsInfo(session=FakeSession()).existing_arns(REGION, kind, ["arn:aws:x"]) is None


def test_failure_raises_with_kind_and_region():
    error = ClientError({"Error": {"Code": "UnauthorizedOperation", "Message": "not allowed"}}, "DescribeVpcs")
    info = AwsInfo(session=FakeSession(ec2=FakeEc2(error=error)))
    with pytest.raises(AwsInfoError) as e:
        info.existing_arns(REGION, "ec2:vpc", [ec2_arn("vpc", "vpc-0a")])
    assert e.value.key == "aws.existence_check_failed"
    assert e.value.params["kind"] == "ec2:vpc" and e.value.params["region"] == REGION
    assert "UnauthorizedOperation" in str(e.value)


def test_results_are_cached_briefly():
    ec2 = FakeEc2([{"VpcId": "vpc-0a"}])
    info = AwsInfo(session=FakeSession(ec2=ec2))
    arns = [ec2_arn("vpc", "vpc-0a")]
    info.existing_arns(REGION, "ec2:vpc", arns)
    info.existing_arns(REGION, "ec2:vpc", arns)
    assert len(ec2.calls) == 1, "画面を開き直すたびに問い合わせ直さない"
