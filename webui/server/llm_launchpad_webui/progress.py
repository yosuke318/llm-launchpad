"""起動の進み具合。terraform apply の実行と、インスタンスが SSM パラメータに書く状態を、画面の段階に並べ直す。

    インスタンスの作成(apply)→ 起動 → Ollama の導入 → モデルの置き場所の用意 → モデルの取得 → 応答の確認 → 使える状態

起動の段階は、apply が終わってから user data が Ollama を入れ始めるまで。残り時間は、段階ごとの目安
(storage.startup_stage_sec)から、今の段階で過ぎた時間を引いて足し合わせる。モデルの取得中は、取得の速さから出す。
"""

import datetime

from .messages import t
from .storage import startup_stage_sec

# 画面に出す段階。creating は terraform apply、そのあとはインスタンスが書く stage
STAGES = ["creating", "booting", "installing_ollama", "preparing_storage", "pulling_model", "verifying", "ready"]

# インスタンスが書く stage(terraform/modules/bootstrap)を、画面の段階に直す。pending は Terraform が作ったときの値
INSTANCE_STAGES = {
    "pending": "booting",
    "instance_started": "booting",
    "installing_ollama": "installing_ollama",
    "preparing_storage": "preparing_storage",
    "pulling_model": "pulling_model",
    "verifying": "verifying",
    "ready": "ready",
}

# 接続のしかたとして画面に出す terraform output
CONNECTION_OUTPUTS = [
    "instance_id",
    "instance_type",
    "availability_zone",
    "purchase_option",
    "access_mode",
    "ollama_endpoint",
    "ssm_port_forward_command",
    "curl_example",
    "openai_api_example",
    "ssm_session_command",
    "status_command",
    "bootstrap_log_path",
    "auto_stop",
]

# 失敗したときに出す terraform の出力の行数
TERRAFORM_LOG_LINES = 40


def parse_time(ts):
    if not ts:
        return None
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def iso(t):
    return None if t is None else t.astimezone(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def cached_model(storage, status):
    """モデルが S3 や EBS にあったか。分からない間は False(取得し直す前提で、長めに見積もる)"""
    cache = (status or {}).get("cache", {})
    if storage == "s3_cache":
        return bool(cache.get("hit"))
    if storage == "ebs":
        # 初期化しなかったボリュームでも、モデルがあるとは限らない。取得の段階に入れば、取得の速さで見積もり直す
        return "formatted" in cache and not cache["formatted"]
    return False


def build_progress(workspace, tfvars, run, status, file_size_gb, now=None, run_lines=None):
    """run は最後の起動(なければ None)、status はインスタンスが書いた状態(なければ None)"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    tfvars = tfvars or {}
    storage = tfvars.get("model_storage", "s3_cache")
    instance_stages = (status or {}).get("stages", {})

    # 各段階の始まり。creating は apply を始めたとき、booting は apply が終わったとき
    started = {
        "creating": parse_time(run.started_at) if run else None,
        "booting": parse_time(run.finished_at) if run and run.status == "succeeded" else None,
    }
    for stage, at in instance_stages.items():
        ui = INSTANCE_STAGES.get(stage)
        if ui and ui != "booting":
            started.setdefault(ui, parse_time(at))
    if status and started["booting"] is None and run is None:
        # サーバーを起動し直して apply の記録がないときは、user data が動き始めた時刻を使う
        started["booting"] = parse_time(instance_stages.get("instance_started"))

    # 今の段階と状態
    error = None
    if run and run.status == "running":
        current, state = "creating", "running"
    elif run and run.status == "failed":
        current, state = "creating", "failed"
        error = {
            "stage": "creating",
            "message": t("apply.failed", code=run.exit_code),
            "terraform_log": (run_lines or run.lines)[-TERRAFORM_LOG_LINES:],
        }
    elif status:
        current = INSTANCE_STAGES.get(status.get("stage"), "booting")
        state = status.get("state", "running")
        if state == "failed":
            error = {"stage": current, "message": status.get("error"), "log_path": status.get("log")}
    elif run:
        # apply は終わったが、状態をまだ読めていない
        current, state = "booting", "running"
    else:
        current, state = None, "unknown"

    index = STAGES.index(current) if current else -1
    stages = []
    for i, stage in enumerate(STAGES):
        if state == "ready" or i < index:
            s = "done"
        elif i == index:
            s = "failed" if state == "failed" else "active"
        else:
            s = "pending"
        finished = started.get(STAGES[i + 1]) if i + 1 < len(STAGES) and s == "done" else None
        if stage == "creating" and run and run.finished_at:
            finished = parse_time(run.finished_at)
        if s == "failed" and stage != "creating":
            # 失敗した段階は、失敗を書いたときまで
            finished = parse_time((status or {}).get("updated_at"))
        stages.append({"id": stage, "status": s, "started_at": iso(started.get(stage)), "finished_at": iso(finished)})

    launch_started = started["creating"] or started.get("booting")
    ready_at = parse_time((status or {}).get("ready_at")) if state == "ready" else None
    failed_at = None
    if state == "failed":
        failed_at = parse_time(run.finished_at) if current == "creating" else parse_time((status or {}).get("updated_at"))
    # 使える状態になったか失敗したら、経過時間はそこで止める
    end = ready_at or failed_at or now
    elapsed = (end - launch_started).total_seconds() if launch_started else None

    pull = (status or {}).get("pull") if current == "pulling_model" else None
    expected = startup_stage_sec(file_size_gb, storage, cached_model(storage, status)) if file_size_gb else None

    return {
        "workspace": workspace,
        "region": tfvars.get("region"),
        "model": tfvars.get("model"),
        "model_storage": storage,
        "purchase_option": tfvars.get("purchase_option"),
        "run": run.summary() if run else None,
        "state": state,
        "stage": current,
        "stages": stages,
        "pull": pull,
        "cache": (status or {}).get("cache"),
        "verify": (status or {}).get("verify"),
        "error": error,
        "started_at": iso(launch_started),
        "ready_at": iso(ready_at),
        "now": iso(now),
        "elapsed_sec": None if elapsed is None else round(elapsed),
        "expected_total_sec": None if expected is None else round(sum(expected.values())),
        "remaining_sec": remaining_sec(expected, current, state, started, now, pull),
    }


def remaining_sec(expected, current, state, started, now, pull):
    """残り時間の目安。目安を過ぎた段階は 0 として数える。使える状態か失敗したときは出さない"""
    if expected is None or current is None or state in ("ready", "failed"):
        return None
    # creating と booting は、apply を始めてから user data が動き始めるまでで1つの目安(実測もそう測った)
    order = ["booting", "installing_ollama", "preparing_storage", "pulling_model", "verifying"]
    key = "booting" if current == "creating" else current
    if key not in order:
        return None
    since = started["creating"] if key == "booting" and started.get("creating") else started.get(key)
    spent = (now - since).total_seconds() if since else 0

    left = max(expected[key] - spent, 0)
    if key == "pulling_model" and pull and pull.get("percent"):
        # 取得の速さが分かれば、目安より実際の速さで出す
        percent = min(float(pull["percent"]), 100.0)
        left = spent * (100 - percent) / percent if percent > 0 else left
    rest = sum(expected[s] for s in order[order.index(key) + 1:])
    return round(left + rest)


def connection_info(outputs):
    """使える状態になったときに出す接続のしかた"""
    if not outputs:
        return None
    return {name: outputs.get(name) for name in CONNECTION_OUTPUTS}
