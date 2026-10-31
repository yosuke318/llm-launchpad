"""AwsInfo の料金の読み取りのテスト。Price List API には接続せず、決まった応答を返す"""

import json

from llm_launchpad_webui.aws_info import AwsInfo


def product(usagetype, unit, usd):
    return json.dumps({
        "product": {"attributes": {"usagetype": usagetype, "endpointType": "PrivateLink"}},
        "terms": {"OnDemand": {"t": {"priceDimensions": {"d": {"unit": unit, "pricePerUnit": {"USD": usd}}}}}},
    })


class FakePricing:
    def __init__(self, price_list):
        self.price_list = price_list
        self.filters = None

    def get_products(self, ServiceCode, Filters):
        self.filters = {f["Field"]: f["Value"] for f in Filters}
        return {"PriceList": self.price_list}


def info_with(pricing):
    info = AwsInfo(session=object())
    info._client = lambda service, region: pricing
    return info


def test_interface_endpoint_hourly_uses_hours_not_bytes():
    pricing = FakePricing([product("APN1-VpcEndpoint-Bytes", "GB", "0.0100000000"), product("APN1-VpcEndpoint-Hours", "Hrs", "0.0140000000")])
    assert info_with(pricing).interface_endpoint_hourly("ap-northeast-1") == 0.014
    assert pricing.filters == {"regionCode": "ap-northeast-1", "productFamily": "VpcEndpoint", "endpointType": "PrivateLink"}


def test_interface_endpoint_hourly_unknown():
    assert info_with(FakePricing([])).interface_endpoint_hourly("ap-northeast-1") is None


def bedrock_product(usagetype, usd):
    return json.dumps({
        "product": {"attributes": {"usagetype": usagetype}},
        "terms": {"OnDemand": {"t": {"priceDimensions": {"d": {"unit": "1M tokens", "pricePerUnit": {"USD": usd}}}}}},
    })


def test_bedrock_token_prices_use_regional_not_global():
    pricing = FakePricing([
        bedrock_product("APN1-MP:APN1_InputTokenCount_Global-Units", "1.0"),
        bedrock_product("APN1-MP:APN1_InputTokenCount-Units", "1.1"),
        bedrock_product("APN1-MP:APN1_OutputTokenCount-Units", "5.5"),
        bedrock_product("APN1-MP:APN1_OutputTokenCount_Global-Units", "5.0"),
        bedrock_product("APN1-MP:APN1_CacheReadInputTokenCount-Units", "0.11"),
    ])
    assert info_with(pricing).bedrock_token_prices("ap-northeast-1", "Claude Haiku 4.5") == {"input": 1.1, "output": 5.5}
    assert pricing.filters == {"regionCode": "ap-northeast-1", "model": "Claude Haiku 4.5", "feature": "On-demand Inference"}


def ec2_product(region, usd):
    return json.dumps({
        "product": {"attributes": {"regionCode": region}},
        "terms": {"OnDemand": {"t": {"priceDimensions": {"d": {"unit": "Hrs", "pricePerUnit": {"USD": usd}}}}}},
    })


class FakePaginatedPricing:
    def __init__(self, pages):
        self.pages = pages
        self.filters = None

    def get_paginator(self, name):
        assert name == "get_products"
        return self

    def paginate(self, ServiceCode, Filters):
        self.filters = {f["Field"]: f["Value"] for f in Filters}
        return [{"PriceList": p} for p in self.pages]


def test_on_demand_hourly_by_region_reads_all_pages():
    pricing = FakePaginatedPricing([[ec2_product("us-east-1", "0.8048")], [ec2_product("ap-northeast-1", "1.1672"), ec2_product("eu-west-1", "0")]])
    assert info_with(pricing).on_demand_hourly_by_region("g6.xlarge") == {"ap-northeast-1": 1.1672, "us-east-1": 0.8048}, "0 ドルのものは料金がないとみなす"
    assert pricing.filters["instanceType"] == "g6.xlarge" and pricing.filters["locationType"] == "AWS Region"


def test_spot_min_by_region_keeps_going_on_errors():
    from llm_launchpad_webui.aws_info import AwsInfoError

    info = AwsInfo(session=object())

    def spot(region, instance_type):
        if region == "eu-west-1":
            raise AwsInfoError("aws.fetch_failed", what="spot", error="AuthFailure")
        return {"us-east-1": {"us-east-1a": 0.5, "us-east-1b": 0.4}, "ap-northeast-1": {}}[region]

    info.spot_hourly_by_az = spot
    found, errors = info.spot_min_by_region(["us-east-1", "ap-northeast-1", "eu-west-1"], "g6.xlarge")
    assert found == {"us-east-1": {"availability_zone": "us-east-1b", "hourly_usd": 0.4}}, "スポット価格のないリージョンは入れない"
    assert len(errors) == 1


class FakeScores:
    def __init__(self):
        self.calls = []

    def get_paginator(self, name):
        assert name == "get_spot_placement_scores"
        return self

    def paginate(self, **kwargs):
        assert kwargs["SingleAvailabilityZone"] is False
        self.calls.append(kwargs["RegionNames"])
        return [{"SpotPlacementScores": [{"Region": r, "Score": 9 if r.startswith("eu") else 1}]} for r in kwargs["RegionNames"]]


def test_region_placement_scores_ten_regions_per_call():
    scores = FakeScores()
    regions = [f"eu-r{i:02d}" for i in range(12)] + ["us-east-1"]
    found = info_with(scores).region_placement_scores(regions, "g6.xlarge")
    assert [len(c) for c in scores.calls] == [10, 3], "1回に返るのは10リージョンまでなので分ける"
    assert len(found) == 13 and found["us-east-1"] == 1 and found["eu-r11"] == 9
