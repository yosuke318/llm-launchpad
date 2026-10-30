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
