"""画面に出す AWS の情報(リージョン、インスタンス型が使えるか、料金、model-store があるか)を問い合わせる。

単価は持たず、その都度 AWS の API から取る(Price List API、スポット価格の履歴)。同じ問い合わせは
しばらく覚えておく。AWS の認証情報は、サーバーを起動した端末のものを使う。読み取りだけを行う。
"""

import datetime
import json
import threading
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

PROJECT = "llm-launchpad"

# Price List API はこのリージョンにある
PRICING_REGION = "us-east-1"


class AwsInfoError(RuntimeError):
    pass


def _first_on_demand_price(price_list):
    for item in price_list:
        product = json.loads(item) if isinstance(item, str) else item
        for term in product["terms"]["OnDemand"].values():
            for dim in term["priceDimensions"].values():
                return float(dim["pricePerUnit"]["USD"])
    return None


class AwsInfo:
    def __init__(self, session=None, ttl_sec=3600):
        self.session = session or boto3.Session()
        self.ttl_sec = ttl_sec
        self._cache = {}
        self._lock = threading.Lock()

    def _client(self, service, region):
        return self.session.client(service, region_name=region)

    def _cached(self, key, fetch, ttl_sec=None):
        """key ごとに、ttl_sec 秒(省略時は self.ttl_sec)だけ結果を覚えておく。
        期限切れのものは書き込むときに消すので、サーバーを長く動かしてもたまり続けない"""
        ttl = self.ttl_sec if ttl_sec is None else ttl_sec
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now < hit[0]:
                return hit[1]
        try:
            value = fetch()
        except (BotoCoreError, ClientError) as e:
            raise AwsInfoError(f"{key[0]} を取れませんでした: {e}") from e
        with self._lock:
            for k in [k for k, (expires, _) in self._cache.items() if expires <= now]:
                del self._cache[k]
            self._cache[key] = (now + ttl, value)
        return value

    def account_id(self):
        return self._cached(("account_id",), lambda: self._client("sts", PRICING_REGION).get_caller_identity()["Account"])

    def regions(self):
        """アカウントで使えるリージョンと、その英語の名前(AWS の公開パラメータから取る)"""
        def fetch():
            codes = sorted(r["RegionName"] for r in self._client("ec2", PRICING_REGION).describe_regions()["Regions"])
            ssm = self._client("ssm", PRICING_REGION)
            names = {}
            # get_parameters は一度に10個まで
            for i in range(0, len(codes), 10):
                chunk = codes[i:i + 10]
                res = ssm.get_parameters(Names=[f"/aws/service/global-infrastructure/regions/{c}/longName" for c in chunk])
                for p in res["Parameters"]:
                    names[p["Name"].split("/")[5]] = p["Value"]
            return [{"code": c, "name": names.get(c, c)} for c in codes]

        return self._cached(("regions",), fetch)

    def offering_azs(self, region, instance_type):
        """そのインスタンス型を提供している AZ"""
        def fetch():
            res = self._client("ec2", region).describe_instance_type_offerings(
                LocationType="availability-zone",
                Filters=[{"Name": "instance-type", "Values": [instance_type]}],
            )
            return sorted(o["Location"] for o in res["InstanceTypeOfferings"])

        return self._cached(("offering_azs", region, instance_type), fetch)

    def on_demand_hourly(self, region, instance_type):
        def fetch():
            res = self._client("pricing", PRICING_REGION).get_products(
                ServiceCode="AmazonEC2",
                Filters=[{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in {
                    "instanceType": instance_type,
                    "regionCode": region,
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "preInstalledSw": "NA",
                    "capacitystatus": "Used",
                }.items()],
            )
            return _first_on_demand_price(res["PriceList"])

        return self._cached(("on_demand_hourly", region, instance_type), fetch)

    def spot_hourly_by_az(self, region, instance_type):
        """AZ ごとの、今のスポット価格"""
        def fetch():
            res = self._client("ec2", region).describe_spot_price_history(
                InstanceTypes=[instance_type],
                ProductDescriptions=["Linux/UNIX"],
                StartTime=datetime.datetime.now(datetime.timezone.utc),
            )
            prices = {}
            for h in res["SpotPriceHistory"]:
                prices.setdefault(h["AvailabilityZone"], float(h["SpotPrice"]))
            return dict(sorted(prices.items()))

        # スポット価格は変わるので、10分だけ覚えておく
        return self._cached(("spot_hourly_by_az", region, instance_type), fetch, ttl_sec=600)

    def gp3_prices(self, region):
        """gp3 の料金。容量(GB-月)、スループット(MiB/s-月)、IOPS(IOPS-月)"""
        def fetch():
            pricing = self._client("pricing", PRICING_REGION)

            def price(family):
                res = pricing.get_products(ServiceCode="AmazonEC2", Filters=[
                    {"Type": "TERM_MATCH", "Field": "volumeApiName", "Value": "gp3"},
                    {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                    {"Type": "TERM_MATCH", "Field": "productFamily", "Value": family},
                ])
                return _first_on_demand_price(res["PriceList"])

            throughput_per_gibps = price("Provisioned Throughput")
            return {
                "storage_per_gb_month": price("Storage"),
                # Price List API は GiBps-月 で返すので、MiBps-月 に直す
                "throughput_per_mibps_month": None if throughput_per_gibps is None else throughput_per_gibps / 1024,
                "iops_per_iops_month": price("System Operation"),
            }

        return self._cached(("gp3_prices", region), fetch)

    def s3_standard_per_gb_month(self, region):
        def fetch():
            res = self._client("pricing", PRICING_REGION).get_products(ServiceCode="AmazonS3", Filters=[
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                {"Type": "TERM_MATCH", "Field": "volumeType", "Value": "Standard"},
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": "Storage"},
            ])
            for item in res["PriceList"]:
                product = json.loads(item) if isinstance(item, str) else item
                for term in product["terms"]["OnDemand"].values():
                    for dim in term["priceDimensions"].values():
                        # 段階料金のうち、最初の段の単価を使う
                        if dim.get("beginRange") == "0":
                            return float(dim["pricePerUnit"]["USD"])
            return None

        return self._cached(("s3_standard_per_gb_month", region), fetch)

    def model_store(self, region):
        """そのリージョンに model-store のバケットとボリュームがあるか(terraform/model-store が作る名前とタグで探す)"""
        def fetch():
            bucket = f"llm-launchpad-models-{self.account_id()}-{region}"
            try:
                self._client("s3", region).head_bucket(Bucket=bucket)
                exists = True
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "403"):
                    raise
                exists = False
            vols = self._client("ec2", region).describe_volumes(Filters=[
                {"Name": "tag:Project", "Values": [PROJECT]},
                {"Name": "tag-key", "Values": ["ModelVolume"]},
            ])["Volumes"]
            volumes = [{
                "name": next((t["Value"] for t in v.get("Tags", []) if t["Key"] == "ModelVolume"), ""),
                "id": v["VolumeId"],
                "availability_zone": v["AvailabilityZone"],
                "size_gib": v["Size"],
                "state": v["State"],
            } for v in vols]
            return {"bucket": bucket, "bucket_exists": exists, "volumes": volumes}

        # model-store を apply した直後に反映されるよう、1分だけ覚えておく
        return self._cached(("model_store", region), fetch, ttl_sec=60)
