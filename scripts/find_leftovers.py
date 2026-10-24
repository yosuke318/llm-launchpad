#!/usr/bin/env python3
"""llm-launchpad のリソースを全リージョンから探し、消し忘れがないかを一覧にする。

ローカルの state ファイルが消えると、リソースだけが残って課金され続ける。管理用タグ(Project=llm-launchpad)
や名前の決まりで AWS 上のリソースを探し、手元の state に載っていないものを「消し忘れ」として目立たせる。

    python3 scripts/find_leftovers.py                     # 全リージョン
    python3 scripts/find_leftovers.py --regions ap-northeast-1 us-east-1
    python3 scripts/find_leftovers.py --profile my-profile --json

終了コード: 0 = 消し忘れなし / 1 = 実行に失敗した / 2 = state に載っていないリソースがある

AWS CLI v2 と標準ライブラリだけを使う。読み取りだけで、リソースを変えたり消したりはしない。
"""

import argparse
import concurrent.futures
import datetime
import glob
import json
import os
import subprocess
import sys

PROJECT = "llm-launchpad"
# IAM ロールとインスタンスプロファイルの name_prefix(terraform/locals.tf の iam_name_prefix)
IAM_PREFIX = "llm-lp-"
# model-store が作るバケットの名前の頭(terraform/model-store/main.tf)
BUCKET_PREFIX = "llm-launchpad-"
HOURS_PER_MONTH = 730

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATE_DIR = os.path.join(HERE, "..", "terraform")


class AwsError(RuntimeError):
    pass


class Aws:
    """AWS CLI を呼ぶ。JSON で返す"""

    def __init__(self, profile=None):
        self.profile = profile

    def __call__(self, service, operation, *args, region=None, ok_errors=()):
        cmd = ["aws", service, operation, *args, "--output", "json"]
        if region:
            cmd += ["--region", region]
        if self.profile:
            cmd += ["--profile", self.profile]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            # バケットにタグがない、などの想定内のエラーは None にする
            if any(e in result.stderr for e in ok_errors):
                return None
            raise AwsError(f"aws {service} {operation} が失敗しました({region or 'global'}): {result.stderr.strip()}")
        return json.loads(result.stdout) if result.stdout.strip() else {}


def tags_of(obj):
    return {t["Key"]: t["Value"] for t in obj.get("Tags") or obj.get("TagSet") or []}


def parse_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def hours_since(value, now):
    return max((now - parse_time(value)).total_seconds() / 3600, 0)


class Prices:
    """料金の目安。単価は持たず、Price List API とスポット価格の履歴から取る(同じ問い合わせは覚えておく)"""

    def __init__(self, aws):
        self.aws = aws
        self.cache = {}

    def _on_demand(self, region, filters):
        args = ["--service-code", "AmazonEC2", "--filters"]
        args += [f"Type=TERM_MATCH,Field={k},Value={v}" for k, v in filters.items()]
        # Price List API は us-east-1 などの一部のリージョンにしかない
        res = self.aws("pricing", "get-products", *args, region="us-east-1")
        for item in res.get("PriceList", []):
            product = json.loads(item) if isinstance(item, str) else item
            for term in product["terms"]["OnDemand"].values():
                for dim in term["priceDimensions"].values():
                    return float(dim["pricePerUnit"]["USD"])
        return None

    def instance_hourly(self, region, instance_type, availability_zone, spot):
        key = ("instance", region, instance_type, availability_zone if spot else None)
        if key not in self.cache:
            if spot:
                res = self.aws(
                    "ec2", "describe-spot-price-history",
                    "--instance-types", instance_type,
                    "--availability-zone", availability_zone,
                    "--product-descriptions", "Linux/UNIX",
                    "--max-items", "1",
                    region=region,
                )
                history = res.get("SpotPriceHistory", [])
                self.cache[key] = float(history[0]["SpotPrice"]) if history else None
            else:
                self.cache[key] = self._on_demand(region, {
                    "instanceType": instance_type,
                    "regionCode": region,
                    "operatingSystem": "Linux",
                    "tenancy": "Shared",
                    "preInstalledSw": "NA",
                    "capacitystatus": "Used",
                })
        return self.cache[key]

    def volume_monthly_per_gb(self, region, volume_type):
        key = ("volume", region, volume_type)
        if key not in self.cache:
            self.cache[key] = self._on_demand(region, {
                "volumeApiName": volume_type,
                "regionCode": region,
                "productFamily": "Storage",
            })
        return self.cache[key]

    def s3_standard_monthly_per_gb(self, region):
        key = ("s3", region)
        if key not in self.cache:
            res = self.aws(
                "pricing", "get-products", "--service-code", "AmazonS3", "--filters",
                f"Type=TERM_MATCH,Field=regionCode,Value={region}",
                "Type=TERM_MATCH,Field=volumeType,Value=Standard",
                "Type=TERM_MATCH,Field=productFamily,Value=Storage",
                region="us-east-1",
            )
            price = None
            for item in res.get("PriceList", []):
                product = json.loads(item) if isinstance(item, str) else item
                for term in product["terms"]["OnDemand"].values():
                    # 段階料金のうち、最初の段(最初の 50TB)の単価を使う
                    for dim in term["priceDimensions"].values():
                        if dim.get("beginRange") == "0":
                            price = float(dim["pricePerUnit"]["USD"])
            self.cache[key] = price
        return self.cache[key]


def resource(region, kind, rid, tags, **fields):
    r = {
        "region": region,
        "type": kind,
        "id": rid,
        "name": tags.get("Name", ""),
        "env_id": tags.get("EnvId", ""),
        "persistent": tags.get("Persistent") == "true",
        "state": "",
        "hours": None,
        "hourly_usd": None,
        "monthly_usd": None,
        "cost_so_far_usd": None,
        "ids": [rid],
        "note": "",
    }
    r.update(fields)
    return r


def find_instances(aws, prices, region, now):
    res = aws(
        "ec2", "describe-instances",
        "--filters", f"Name=tag:Project,Values={PROJECT}",
        "Name=instance-state-name,Values=pending,running,stopping,stopped",
        region=region,
    )
    found = []
    for reservation in res.get("Reservations", []):
        for inst in reservation["Instances"]:
            tags = tags_of(inst)
            state = inst["State"]["Name"]
            spot = inst.get("InstanceLifecycle") == "spot"
            r = resource(
                region, "ec2-instance", inst["InstanceId"], tags,
                state=f"{state} ({'spot' if spot else 'on-demand'}, {inst['InstanceType']})",
                ids=[inst["InstanceId"]],
            )
            if state == "running":
                # LaunchTime は停止から再開するたびに変わるので、今回の起動からの時間になる
                r["hours"] = round(hours_since(inst["LaunchTime"], now), 1)
                price = prices.instance_hourly(region, inst["InstanceType"], inst["Placement"]["AvailabilityZone"], spot)
                if price is not None:
                    r["hourly_usd"] = price
                    r["cost_so_far_usd"] = round(price * r["hours"], 2)
            else:
                r["note"] = "停止中はインスタンスの料金はかからない(EBS の料金はかかる)"
            if inst.get("SpotInstanceRequestId"):
                r["ids"].append(inst["SpotInstanceRequestId"])
            found.append(r)
    return found


def find_volumes(aws, prices, region, now):
    res = aws("ec2", "describe-volumes", "--filters", f"Name=tag:Project,Values={PROJECT}", region=region)
    found = []
    for vol in res.get("Volumes", []):
        tags = tags_of(vol)
        attached = [a["InstanceId"] for a in vol.get("Attachments", [])]
        r = resource(
            region, "ebs-volume", vol["VolumeId"], tags,
            state=f"{vol['State']} ({vol['VolumeType']}, {vol['Size']} GiB)",
            hours=round(hours_since(vol["CreateTime"], now), 1),
            note=f"付いているインスタンス: {', '.join(attached)}" if attached else "どのインスタンスにも付いていない",
        )
        per_gb = prices.volume_monthly_per_gb(region, vol["VolumeType"])
        if per_gb is not None:
            # スループットや IOPS を上げた分は含めない
            r["monthly_usd"] = round(per_gb * vol["Size"], 2)
            r["cost_so_far_usd"] = round(r["monthly_usd"] * r["hours"] / HOURS_PER_MONTH, 2)
        found.append(r)
    return found


def find_vpcs(aws, region):
    res = aws("ec2", "describe-vpcs", "--filters", f"Name=tag:Project,Values={PROJECT}", region=region)
    return [
        resource(region, "vpc", vpc["VpcId"], tags_of(vpc), state=vpc["CidrBlock"], note="VPC 自体は無料")
        for vpc in res.get("Vpcs", [])
    ]


def find_spot_requests(aws, region, instance_ids):
    """残ったスポットリクエスト。persistent のリクエストは、インスタンスを消してもリクエストが残ると再び起動される"""
    res = aws(
        "ec2", "describe-spot-instance-requests",
        "--filters", "Name=state,Values=open,active,disabled",
        region=region,
    )
    found = []
    for req in res.get("SpotInstanceRequests", []):
        profile = (req.get("LaunchSpecification", {}).get("IamInstanceProfile") or {}).get("Name", "")
        arn = (req.get("LaunchSpecification", {}).get("IamInstanceProfile") or {}).get("Arn", "")
        ours = req.get("InstanceId") in instance_ids or profile.startswith(IAM_PREFIX) or f"instance-profile/{IAM_PREFIX}" in arn
        if not ours:
            continue
        found.append(resource(
            region, "spot-request", req["SpotInstanceRequestId"], tags_of(req),
            state=f"{req['State']} ({req.get('Type', '')})",
            note=f"インスタンス: {req.get('InstanceId', 'なし')}",
        ))
    return found


def find_parameters(aws, region):
    res = aws(
        "ssm", "describe-parameters",
        "--parameter-filters", f"Key=tag:Project,Values={PROJECT}",
        region=region,
    )
    found = []
    for p in res.get("Parameters", []):
        # 名前は /llm-launchpad/<環境ID>/status
        parts = p["Name"].strip("/").split("/")
        env_id = parts[1] if len(parts) > 2 and parts[0] == PROJECT else ""
        found.append(resource(region, "ssm-parameter", p["Name"], {"EnvId": env_id}, state=p.get("Tier", ""), note="Standard は無料"))
    return found


def find_schedules(aws, region):
    res = aws("scheduler", "list-schedules", "--name-prefix", IAM_PREFIX, region=region)
    return [
        resource(region, "schedule", s["Name"], {}, state=s.get("State", ""), ids=[s["Name"], s.get("Arn", "")])
        for s in res.get("Schedules", [])
    ]


def find_buckets(aws, prices):
    """S3 バケットはリージョンによらず一覧できる。Project タグか名前の決まりで見分ける"""
    res = aws("s3api", "list-buckets")
    found = []
    for b in res.get("Buckets", []):
        name = b["Name"]
        tagging = aws("s3api", "get-bucket-tagging", "--bucket", name, ok_errors=("NoSuchTagSet", "AccessDenied", "NoSuchBucket"))
        tags = tags_of(tagging or {})
        if tags.get("Project") != PROJECT and not name.startswith(BUCKET_PREFIX):
            continue
        loc = aws("s3api", "get-bucket-location", "--bucket", name, ok_errors=("NoSuchBucket",)) or {}
        region = loc.get("LocationConstraint") or "us-east-1"
        r = resource(region, "s3-bucket", name, tags, note="")
        size = bucket_size_bytes(aws, region, name)
        if size is not None:
            gb = size / 1e9
            r["state"] = f"{gb:.1f} GB"
            per_gb = prices.s3_standard_monthly_per_gb(region)
            if per_gb is not None:
                r["monthly_usd"] = round(per_gb * gb, 2)
        else:
            r["state"] = "大きさは不明(CloudWatch のメトリクスが1日1回しか出ないため、作った直後は出ない)"
        found.append(r)
    return found


def bucket_size_bytes(aws, region, bucket):
    now = datetime.datetime.now(datetime.timezone.utc)
    res = aws(
        "cloudwatch", "get-metric-statistics",
        "--namespace", "AWS/S3", "--metric-name", "BucketSizeBytes",
        "--dimensions", f"Name=BucketName,Value={bucket}", "Name=StorageType,Value=StandardStorage",
        "--start-time", (now - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--end-time", now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "--period", "86400", "--statistics", "Average",
        region=region,
    )
    points = sorted(res.get("Datapoints", []), key=lambda p: p["Timestamp"])
    return points[-1]["Average"] if points else None


def find_iam_roles(aws):
    res = aws("iam", "list-roles")
    return [
        resource("global", "iam-role", role["RoleName"], {}, ids=[role["RoleName"], role["Arn"]], note="IAM は無料")
        for role in res.get("Roles", [])
        if role["RoleName"].startswith(IAM_PREFIX)
    ]


def find_in_region(aws, prices, region, now):
    instances = find_instances(aws, prices, region, now)
    instance_ids = {i["id"] for i in instances}
    return (
        instances
        + find_volumes(aws, prices, region, now)
        + find_spot_requests(aws, region, instance_ids)
        + find_vpcs(aws, region)
        + find_parameters(aws, region)
        + find_schedules(aws, region)
    )


def strings_in(value):
    """state の属性にある文字列をすべて返す。ルートボリュームのIDのように、入れ子の中にあるものも拾う"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings_in(v)


def state_ids(state_dir):
    """手元の state ファイルにある、リソースのIDや名前。terraform/ の下の環境、model-store、budget を読む"""
    patterns = ["terraform.tfstate", "terraform.tfstate.d/*/terraform.tfstate", "*/terraform.tfstate", "*/terraform.tfstate.d/*/terraform.tfstate"]
    ids = set()
    files = []
    for pattern in patterns:
        for path in glob.glob(os.path.join(state_dir, pattern)):
            files.append(path)
            with open(path, encoding="utf-8") as f:
                state = json.load(f)
            for res in state.get("resources", []):
                for inst in res.get("instances", []):
                    ids.update(s for s in strings_in(inst.get("attributes") or {}) if s)
    return ids, sorted(set(files))


def mark_managed(resources, known_ids):
    for r in resources:
        r["in_state"] = any(i in known_ids for i in r["ids"] if i)
    return resources


def money(v):
    return "-" if v is None else f"${v:,.2f}"


def print_report(resources, regions, state_files, errors, out=None):
    out = out or sys.stdout
    print(f"llm-launchpad のリソース({len(regions)} リージョンを確認)", file=out)
    print(f"照らし合わせた state: {len(state_files)} 個" + "".join(f"\n  {p}" for p in state_files), file=out)
    if not resources:
        print("\n見つかったリソースはありません。", file=out)
    by_region = {}
    for r in resources:
        by_region.setdefault(r["region"], []).append(r)
    for region in sorted(by_region, key=lambda x: (x == "global", x)):
        print(f"\n== {region}", file=out)
        for r in sorted(by_region[region], key=lambda x: (x["in_state"], x["type"], x["id"])):
            mark = "  " if r["in_state"] else "!!"
            label = "state にある" if r["in_state"] else ("残す設定(Persistent)" if r["persistent"] else "state にない")
            env = f" env={r['env_id']}" if r["env_id"] else ""
            print(f"{mark} {r['type']:<14} {r['id']}{env}  [{label}]", file=out)
            details = [r["state"]] if r["state"] else []
            if r["hours"] is not None:
                details.append(f"{r['hours']} 時間")
            if r["hourly_usd"] is not None:
                details.append(f"{money(r['hourly_usd'])}/時")
            if r["monthly_usd"] is not None:
                details.append(f"{money(r['monthly_usd'])}/月")
            if r["cost_so_far_usd"] is not None:
                details.append(f"ここまで約 {money(r['cost_so_far_usd'])}")
            if details:
                print(f"     {' / '.join(details)}", file=out)
            if r["note"]:
                print(f"     {r['note']}", file=out)

    unmanaged = [r for r in resources if not r["in_state"] and not r["persistent"]]
    hourly = sum(r["hourly_usd"] or 0 for r in resources if r["hours"] is not None and r["type"] == "ec2-instance")
    monthly = sum(r["monthly_usd"] or 0 for r in resources)
    print("\n== まとめ", file=out)
    print(f"見つかったリソース: {len(resources)} 件 / state にない(残す設定のものを除く): {len(unmanaged)} 件", file=out)
    print(f"動いているインスタンスの料金: 約 {money(hourly)}/時 / 保管の料金: 約 {money(monthly)}/月", file=out)
    print("料金は目安。スループットや IOPS を上げた分、データ転送、リクエストの料金は含めない", file=out)
    if unmanaged:
        print("state にないリソースは消し忘れの可能性がある。消すときは AWS のコンソールか CLI で確かめてから消す", file=out)
    for e in errors:
        print(f"[エラー] {e}", file=out)


def opted_in_regions(aws):
    res = aws("ec2", "describe-regions", region="us-east-1")
    return sorted(r["RegionName"] for r in res.get("Regions", []))


def main(argv=None, aws=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--regions", nargs="+", help="探すリージョン。省略すると、アカウントで使える全リージョン")
    parser.add_argument("--profile", help="AWS CLI のプロファイル名")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="state ファイルを探すディレクトリ(既定は terraform/)")
    parser.add_argument("--json", action="store_true", help="JSON で出す")
    parser.add_argument("--workers", type=int, default=8, help="同時に調べるリージョンの数")
    args = parser.parse_args(argv)

    aws = aws or Aws(args.profile)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    prices = Prices(aws)
    errors = []

    try:
        regions = args.regions or opted_in_regions(aws)
    except AwsError as e:
        print(e, file=sys.stderr)
        return 1

    resources = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(find_in_region, aws, prices, region, now): region for region in regions}
        for future in concurrent.futures.as_completed(futures):
            try:
                resources += future.result()
            except AwsError as e:
                errors.append(str(e))
    for finder in (lambda: find_buckets(aws, prices), lambda: find_iam_roles(aws)):
        try:
            resources += finder()
        except AwsError as e:
            errors.append(str(e))

    known_ids, state_files = state_ids(args.state_dir)
    mark_managed(resources, known_ids)

    if args.json:
        json.dump({"regions": regions, "state_files": state_files, "resources": resources, "errors": errors}, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print_report(resources, regions, state_files, errors)

    if errors:
        return 1
    return 2 if any(not r["in_state"] and not r["persistent"] for r in resources) else 0


if __name__ == "__main__":
    sys.exit(main())
