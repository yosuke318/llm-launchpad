"""起動前の事前チェック。認証情報、インスタンス型を使えるか、vCPU クォータが足りるかを確かめる。

1つでも ng があれば、その購入方法(スポット / オンデマンド)では起動に進めない。
確かめられなかったもの(権限がないなど)は unknown にして、進むのは止めない。
"""

from .aws_info import AwsInfoError

# G 系と VT 系のインスタンスの vCPU クォータ(オンデマンドとスポットは別枠)
QUOTAS = {
    "on_demand": {"code": "L-DB2E81BA", "name": "Running On-Demand G and VT instances"},
    "spot": {"code": "L-3819A6DF", "name": "All G and VT Spot Instance Requests"},
}


def quota_console_url(region, quota_code):
    """引き上げを申請するページ"""
    return f"https://{region}.console.aws.amazon.com/servicequotas/home/services/ec2/quotas/{quota_code}"


def check(id, status, **detail):
    return {"id": id, "status": status, **detail}


def run_preflight(info, region, instance_type, catalog_vcpus=None):
    checks = []

    try:
        identity = info.caller_identity()
        checks.append(check("credentials", "ok", account=identity["account"], arn=identity["arn"], profile=identity.get("profile")))
    except AwsInfoError as e:
        # 認証情報がなければ、ほかのチェックもできない
        checks.append(check("credentials", "ng", error=str(e)))
        return {"region": region, "instance_type": instance_type, "checks": checks, "can_proceed": {"spot": False, "on_demand": False}}

    try:
        azs = info.offering_azs(region, instance_type)
        checks.append(check("instance_type", "ok" if azs else "ng", availability_zones=azs))
    except AwsInfoError as e:
        checks.append(check("instance_type", "unknown", error=str(e)))

    try:
        needed = info.instance_vcpus(region, instance_type)
    except AwsInfoError:
        # インスタンス型の表(catalog/instance_types.yaml)の値で代える
        needed = catalog_vcpus

    in_use_error = None
    try:
        in_use = info.g_and_vt_vcpus_in_use(region)
    except AwsInfoError as e:
        in_use = None
        in_use_error = str(e)

    for option, q in QUOTAS.items():
        detail = {"option": option, "quota_code": q["code"], "quota_name": q["name"], "needed_vcpus": needed,
                  "request_url": quota_console_url(region, q["code"])}
        try:
            limit = info.service_quota(region, q["code"])
        except AwsInfoError as e:
            checks.append(check(f"quota_{option}", "unknown", error=str(e), **detail))
            continue
        used = in_use[option] if in_use else None
        try:
            pending = info.open_quota_requests(region, q["code"])
        except AwsInfoError:
            pending = []
        detail.update(limit_vcpus=limit, used_vcpus=used, pending_requests=pending)
        if needed is None or used is None:
            checks.append(check(f"quota_{option}", "unknown", error=in_use_error if used is None else "vCPU の数が分かりません", **detail))
        else:
            detail["available_vcpus"] = limit - used
            checks.append(check(f"quota_{option}", "ok" if limit - used >= needed else "ng", **detail))

    def blocked(option):
        return any(c["status"] == "ng" for c in checks if c["id"] in ("credentials", "instance_type", f"quota_{option}"))

    return {
        "region": region,
        "instance_type": instance_type,
        "checks": checks,
        "can_proceed": {option: not blocked(option) for option in QUOTAS},
    }
