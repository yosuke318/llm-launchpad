"""環境の一覧と消し忘れ。terraform の state、画面から起動したときの tfvars、AWS にあるインスタンスとタグを突き合わせる。

    環境: terraform のワークスペースのうち、state にリソースがあるか、画面から起動した(tfvars がある)もの
    消し忘れ: 管理用タグ(Project=llm-launchpad)が付いているのに、どの state にもないリソース。
              model-store や budget のように、環境を消しても残すもの(Persistent=true)は除く。
              タグで見つけたものは、種類ごとの API で今もあるか確かめてから数える

料金の目安は、インスタンスを最後に動かし始めてからの時間に、今の1時間あたりの料金を掛けたもの。
止めていた間やディスクの料金は入れない。
"""

import datetime

from .messages import MessageError

# 環境を消しても残すリソースに付けるタグ(terraform/model-store、terraform/budget)
PERSISTENT_TAG = "Persistent"


def parse_time(ts):
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _strings(value):
    """state の属性に出てくる文字列を全部(ID や ARN は入れ子の中にもある。ルートボリュームの ID など)"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)


def managed_resources(state):
    """state にある、terraform が作ったリソース(data ソースは除く)"""
    return [r for r in (state or {}).get("resources", []) if r.get("mode") == "managed"]


def state_identifiers(state):
    """state にあるリソースの ID と ARN"""
    found = set()
    for r in managed_resources(state):
        for inst in r.get("instances", []):
            found.update(_strings(inst.get("attributes", {})))
    return found


def state_instance(state):
    """state にある GPU インスタンス(modules/instance の aws_instance)の属性。なければ None"""
    for r in managed_resources(state):
        if r.get("type") == "aws_instance":
            for inst in r.get("instances", []):
                return inst.get("attributes", {})
    return None


def arn_resource_id(arn):
    """ARN の最後の部分(arn:aws:ec2:...:instance/i-0abc の i-0abc)"""
    return arn.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def arn_resource_type(arn):
    """ARN のサービスと種類(ec2:instance、ssm:parameter など)"""
    parts = arn.split(":", 5)
    if len(parts) < 6:
        return arn
    kind = parts[5].split("/", 1)[0]
    return f"{parts[2]}:{kind}" if kind != parts[5] else parts[2]


def drop_deleted(resources, existing_arns):
    """resources({region, arn, ...} の一覧。find_leftovers の結果など)から、もう消えたリソースを除く。
    タグ付けの API は、terraform destroy で消したリソースもしばらく返す。
    existing_arns(region, 種類, ARN の一覧) は今もある ARN を返す(確かめ方のない種類は None)。
    確かめられなかった種類はそのまま残し、そのエラーを返す"""
    groups = {}
    for r in resources:
        groups.setdefault((r["region"], arn_resource_type(r["arn"])), set()).add(r["arn"])
    gone, errors = set(), []
    for (region, kind), arns in groups.items():
        try:
            existing = existing_arns(region, kind, sorted(arns))
        except MessageError as e:
            errors.append(e)
            continue
        if existing is not None:
            gone |= arns - set(existing)
    return [r for r in resources if r["arn"] not in gone], errors


def find_leftovers(tagged, known, live_instance_ids, skip_env_ids=()):
    """tagged は tagged_resources の結果({region, arn, tags})、known は全部の state の ID と ARN。
    skip_env_ids は、state を読めなかったか apply の途中で、消し忘れかどうか決められない環境"""
    leftovers = []
    for r in tagged:
        tags = r["tags"]
        if tags.get(PERSISTENT_TAG) == "true" or tags.get("EnvId") in skip_env_ids:
            continue
        kind = arn_resource_type(r["arn"])
        rid = arn_resource_id(r["arn"])
        # タグ付けの API は、終了したインスタンスをしばらく返すことがある
        if kind == "ec2:instance" and rid not in live_instance_ids:
            continue
        if r["arn"] in known or rid in known:
            continue
        leftovers.append({
            "region": r["region"],
            "arn": r["arn"],
            "resource_type": kind,
            "resource_id": rid,
            "env_id": tags.get("EnvId"),
            "name": tags.get("Name"),
            "created_at": tags.get("CreatedAt"),
        })
    return sorted(leftovers, key=lambda x: (x["region"], x["env_id"] or "", x["resource_type"], x["resource_id"]))


def hourly_usd(prices, instance):
    """prices は {"on_demand": 単価, "spot_by_az": {AZ: 単価}}"""
    if not prices or not instance:
        return None
    if instance["purchase_option"] == "spot":
        return (prices.get("spot_by_az") or {}).get(instance["availability_zone"])
    return prices.get("on_demand")


def build_environment(workspace, tfvars, state, instance, run, prices, now, default_region):
    """一覧の1行。instance は AWS で見たインスタンス(project_instances の1つ)、run はサーバーのメモリにある最後の起動か削除"""
    tfvars = tfvars or {}
    attrs = state_instance(state) or {}
    resources = managed_resources(state)
    running = instance is not None and instance["state"] == "running"
    launched = parse_time(instance["launch_time"]) if running else None
    elapsed = (now - launched).total_seconds() if launched else None
    hourly = hourly_usd(prices, instance) if running else None

    return {
        "workspace": workspace,
        "region": attrs.get("region") or tfvars.get("region") or default_region,
        "model": tfvars.get("model"),
        "instance_type": (instance or {}).get("instance_type") or attrs.get("instance_type") or tfvars.get("instance_type"),
        "purchase_option": (instance or {}).get("purchase_option") or tfvars.get("purchase_option"),
        "resource_count": len(resources),
        # 起動したときの tfvars がないと destroy に変数を渡せない(terraform を手で実行して作った環境)
        "has_tfvars": bool(tfvars),
        "instance": instance,
        "elapsed_sec": None if elapsed is None else round(elapsed),
        "hourly_usd": hourly,
        "cost_usd": None if hourly is None or elapsed is None else round(hourly * elapsed / 3600, 4),
        "run": run.summary() if run else None,
    }
