"""比較モード。同じモデルを、複数の構成(インスタンス型 / モデルの置き場所 / 購入方法)で1つずつ順に起動して測る。

    構成ごとに: 起動(init と apply)→ 使える状態になるまで待つ → 計測 → 削除(init と destroy)→ 次の構成

費用を抑えるため、同時に動かす環境は1つだけにし、測り終えたら(失敗しても)削除してから次へ進む。
削除に失敗したら、環境が残ったまま次を起動しないよう、そこで止める(残った環境は環境の一覧から消す)。

terraform と AWS への呼び出しは ops(app.py の ComparisonOps)にまとめ、テストでは偽物に差し替える。
比較の記録はサーバーのメモリにだけ持つ。測った結果はベンチマークの履歴に comparison_id を付けて残る。
"""

import asyncio
import csv
import datetime
import io
import re
import time
import uuid

from .benchmark import CSV_COLUMNS, MAX_RUNS
from .messages import MessageError

# 一度に比べられる構成の数
MIN_CONFIGS = 2
MAX_CONFIGS = 6

STORAGES = ("s3_cache", "ebs", "download")
PURCHASE_OPTIONS = ("spot", "on-demand")
NETWORK_MODES = ("public", "private")

# 使える状態になるまで待つ長さ。実機では大きいモデルの取得に20分ほどかかる(storage.py の測定)
DEFAULT_READY_TIMEOUT_SEC = 60 * 60
# ほかの起動や削除(画面から始めたもの)が終わるのを待つ長さ
DEFAULT_BUSY_TIMEOUT_SEC = 30 * 60
DEFAULT_POLL_SEC = 10
# 削除に失敗したときに、もう一度だけ試す
DESTROY_ATTEMPTS = 2

REGION_PATTERN = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")

# 比較の CSV。構成ごとの行に、ベンチマークの列を続ける
COMPARISON_CSV_COLUMNS = [
    "comparison_id", "index", "config_workspace", "config_instance_type", "config_model_storage", "config_purchase_option",
    "status", "error", "startup_sec", "elapsed_sec", "run_cost_usd",
    *[c for c in CSV_COLUMNS if c != "comparison_id"],
]


class ComparisonError(MessageError):
    pass


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def workspace_name(comparison_id, index):
    """構成ごとの環境ID。環境IDの規則(英小文字・数字・ハイフンの20文字以内)に収まる。例: cmp-1a2b3c-2"""
    return f"cmp-{comparison_id[:6]}-{index}"


def validate(request, catalog_models, fitting_instance_types):
    """比較の頼みを確かめる。fitting_instance_types は、そのモデルとコンテキスト長で VRAM に収まるインスタンス型"""
    if request["model"] not in catalog_models:
        raise ComparisonError("model.unknown", model=request["model"])
    if not REGION_PATTERN.match(request["region"]):
        raise ComparisonError("comparison.region_invalid")
    if request["network_mode"] not in NETWORK_MODES:
        raise ComparisonError("comparison.network_invalid")
    if not 1 <= request["runs"] <= MAX_RUNS:
        raise ComparisonError("bench.runs_range", max=MAX_RUNS)
    idle = request["idle_stop_minutes"]
    if not (idle == 0 or 10 <= idle <= 1440):
        raise ComparisonError("comparison.idle_stop_invalid")
    # サーバーが途中で止まっても環境が動き続けないよう、最大稼働時間は必ず付ける
    if not 1 <= request["max_lifetime_hours"] <= 720:
        raise ComparisonError("comparison.max_lifetime_invalid")

    configs = request["configs"]
    if not MIN_CONFIGS <= len(configs) <= MAX_CONFIGS:
        raise ComparisonError("comparison.config_count", min=MIN_CONFIGS, max=MAX_CONFIGS)
    seen = set()
    for c in configs:
        key = (c["instance_type"], c["model_storage"], c["purchase_option"])
        if c["instance_type"] not in fitting_instance_types:
            raise ComparisonError("comparison.instance_type_not_fit", instance_type=c["instance_type"], model=request["model"])
        if c["model_storage"] not in STORAGES:
            raise ComparisonError("comparison.storage_invalid", storage=c["model_storage"])
        if c["model_storage"] == "download" and request["network_mode"] == "private":
            raise ComparisonError("comparison.download_private")
        if c["purchase_option"] not in PURCHASE_OPTIONS:
            raise ComparisonError("comparison.purchase_invalid", purchase=c["purchase_option"])
        if key in seen:
            raise ComparisonError("comparison.duplicate_config", instance_type=key[0], storage=key[1], purchase=key[2])
        seen.add(key)


def new_comparison(request):
    comparison_id = uuid.uuid4().hex[:12]
    return {
        "id": comparison_id,
        "model": request["model"],
        "region": request["region"],
        "network_mode": request["network_mode"],
        "context_length": request["context_length"],
        "kv_cache_type": request["kv_cache_type"],
        "runs": request["runs"],
        "idle_stop_minutes": request["idle_stop_minutes"],
        "max_lifetime_hours": request["max_lifetime_hours"],
        # running / succeeded(全部の構成で測れた)/ partial(測れなかった構成がある)/ failed(削除できずに止めた)/ canceled
        "status": "running",
        "cancel_requested": False,
        "error": None,
        "started_at": now_iso(),
        "finished_at": None,
        "configs": [
            {
                "index": i,
                "workspace": workspace_name(comparison_id, i),
                "instance_type": c["instance_type"],
                "model_storage": c["model_storage"],
                "purchase_option": c["purchase_option"],
                # pending / launching / starting / benchmarking / destroying / done / failed / skipped
                "status": "pending",
                "stage": None,
                "error": None,
                # 削除できたか。起動しなかった構成は None
                "destroyed": None,
                "startup_sec": None,
                "started_at": None,
                "finished_at": None,
                "elapsed_sec": None,
                "run_cost_usd": None,
                "launch_run_id": None,
                "destroy_run_id": None,
                "result": None,
            }
            for i, c in enumerate(request["configs"], start=1)
        ],
    }


def launch_variables(comparison, config):
    """構成ごとの tfvars に書く値。AZ は書かず、インスタンス型を提供しているAZから terraform が選ぶ"""
    return {
        "model": comparison["model"],
        "region": comparison["region"],
        "context_length": comparison["context_length"],
        "kv_cache_type": comparison["kv_cache_type"],
        "network_mode": comparison["network_mode"],
        "idle_stop_minutes": comparison["idle_stop_minutes"],
        "max_lifetime_hours": comparison["max_lifetime_hours"],
        "instance_type": config["instance_type"],
        "model_storage": config["model_storage"],
        "purchase_option": config["purchase_option"],
    }


def elapsed_between(start_iso, end_iso):
    if not start_iso or not end_iso:
        return None
    start = datetime.datetime.fromisoformat(start_iso)
    end = datetime.datetime.fromisoformat(end_iso)
    return round((end - start).total_seconds())


def run_cost(hourly_usd, elapsed_sec):
    """起動を始めてから削除し終えるまでの時間に、1時間あたりの料金を掛けた目安"""
    if hourly_usd is None or elapsed_sec is None:
        return None
    return round(hourly_usd * elapsed_sec / 3600, 4)


class Canceled(Exception):
    pass


async def run(comparison, ops, poll_sec=DEFAULT_POLL_SEC, ready_timeout_sec=DEFAULT_READY_TIMEOUT_SEC,
              busy_timeout_sec=DEFAULT_BUSY_TIMEOUT_SEC):
    """構成を1つずつ順に、起動して測って削除する。

    ops は次の非同期の関数を持つ:
        launch(workspace, variables) -> Run      init と apply を始める。ほかの実行中なら runner.BusyError
        destroy(workspace) -> Run                init と destroy を始める。ほかの実行中なら runner.BusyError
        wait(run)                                Run が終わるまで待つ
        progress(workspace) -> dict              起動の進み具合(progress.build_progress の形)
        benchmark(workspace, comparison, config) -> dict   測って、保存した結果を返す
        is_busy_error(e) -> bool                 ほかの実行中で始められなかったエラーか
    """
    try:
        for config in comparison["configs"]:
            if comparison["cancel_requested"]:
                break
            if not await run_one(comparison, config, ops, poll_sec, ready_timeout_sec, busy_timeout_sec):
                # 削除できなかった。環境が残ったまま次を起動しない
                comparison["status"] = "failed"
                comparison["error"] = ComparisonError("comparison.destroy_failed", workspace=config["workspace"])
                break
    except Exception as e:  # noqa: BLE001 途中で想定外のことが起きても、running のまま残さない
        comparison["status"] = "failed"
        comparison["error"] = ComparisonError("comparison.unexpected", error=f"{type(e).__name__}: {e}")
    finally:
        for config in comparison["configs"]:
            if config["status"] == "pending":
                config["status"] = "skipped"
        if comparison["status"] == "running":
            if comparison["cancel_requested"]:
                comparison["status"] = "canceled"
            elif all(c["status"] == "done" for c in comparison["configs"]):
                comparison["status"] = "succeeded"
            else:
                comparison["status"] = "partial"
        comparison["finished_at"] = now_iso()


async def run_one(comparison, config, ops, poll_sec, ready_timeout_sec, busy_timeout_sec):
    """1つの構成を起動して測り、削除する。削除できたら True"""
    config["started_at"] = now_iso()
    config["status"] = "launching"
    launched = False
    try:
        run_ = await start_when_idle(lambda: ops.launch(config["workspace"], launch_variables(comparison, config)),
                                     ops, comparison, poll_sec, busy_timeout_sec)
        launched = True
        config["launch_run_id"] = run_.id
        config["stage"] = "creating"
        await ops.wait(run_)

        config["status"] = "starting"
        await wait_ready(comparison, config, ops, poll_sec, ready_timeout_sec)

        if comparison["cancel_requested"]:
            raise Canceled()
        config["status"] = "benchmarking"
        config["result"] = await ops.benchmark(config["workspace"], comparison, config)
        config["status"] = "done"
    except Canceled:
        config["status"] = "skipped"
        config["error"] = ComparisonError("comparison.canceled")
    except MessageError as e:
        config["status"] = "failed"
        config["error"] = e
    except Exception as e:  # noqa: BLE001 構成1つが想定外の理由で失敗しても、削除は必ずする
        config["status"] = "failed"
        config["error"] = ComparisonError("comparison.unexpected", error=f"{type(e).__name__}: {e}")

    if not launched:
        # apply を始めていなければ、作ったものはない
        config["finished_at"] = now_iso()
        return True

    succeeded = config["status"] == "done"
    status_after = config["status"]
    config["status"] = "destroying"
    destroyed = await destroy(config, ops, comparison, poll_sec, busy_timeout_sec)
    config["destroyed"] = destroyed
    config["status"] = status_after
    if not destroyed and succeeded:
        # 測れても、削除できなければ失敗として残す
        config["status"] = "failed"
        config["error"] = ComparisonError("comparison.destroy_failed", workspace=config["workspace"])
    config["finished_at"] = now_iso()
    config["elapsed_sec"] = elapsed_between(config["started_at"], config["finished_at"])
    config["run_cost_usd"] = run_cost((config["result"] or {}).get("hourly_usd"), config["elapsed_sec"])
    return destroyed


async def destroy(config, ops, comparison, poll_sec, busy_timeout_sec):
    for _ in range(DESTROY_ATTEMPTS):
        try:
            # 取り消しても削除は待つ(費用がかかり続けないように)
            run_ = await start_when_idle(lambda: ops.destroy(config["workspace"]), ops, None, poll_sec, busy_timeout_sec)
        except MessageError as e:
            config["error"] = config["error"] or e
            continue
        config["destroy_run_id"] = run_.id
        await ops.wait(run_)
        if run_.status == "succeeded":
            return True
    return False


async def start_when_idle(start, ops, comparison, poll_sec, busy_timeout_sec):
    """ほかの起動や削除が動いていれば、終わるのを待ってから始める(terraform は同時に1つしか動かせない)"""
    deadline = time.monotonic() + busy_timeout_sec
    while True:
        try:
            return await start()
        except Exception as e:
            if not ops.is_busy_error(e):
                raise
        if comparison is not None and comparison["cancel_requested"]:
            raise Canceled()
        if time.monotonic() > deadline:
            raise ComparisonError("comparison.busy_timeout", sec=busy_timeout_sec)
        await asyncio.sleep(poll_sec)


async def wait_ready(comparison, config, ops, poll_sec, ready_timeout_sec):
    """使える状態になるまで、起動の進み具合を読み直す。失敗したら、止まった段階の理由で ComparisonError にする"""
    deadline = time.monotonic() + ready_timeout_sec
    while True:
        p = await ops.progress(config["workspace"])
        config["stage"] = p.get("stage")
        if p.get("state") == "ready":
            config["startup_sec"] = p.get("elapsed_sec")
            return
        if p.get("state") == "failed":
            error = p.get("error") or {}
            raise ComparisonError("comparison.launch_failed", stage=error.get("stage") or p.get("stage") or "?",
                                  error=error.get("message") or "-")
        if comparison["cancel_requested"]:
            raise Canceled()
        if time.monotonic() > deadline:
            raise ComparisonError("comparison.ready_timeout", sec=ready_timeout_sec)
        await asyncio.sleep(poll_sec)


def summary(comparison):
    """画面に返す形。エラーは、読んだ人の言語で文字にする"""
    def text(e):
        return None if e is None else str(e)

    return {
        **{k: v for k, v in comparison.items() if k not in ("configs", "error")},
        "error": text(comparison["error"]),
        "configs": [{**{k: v for k, v in c.items() if k != "error"}, "error": text(c["error"])} for c in comparison["configs"]],
    }


def to_csv(comparison):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COMPARISON_CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for c in summary(comparison)["configs"]:
        writer.writerow({
            **(c["result"] or {}),
            "comparison_id": comparison["id"],
            "index": c["index"],
            "config_workspace": c["workspace"],
            "config_instance_type": c["instance_type"],
            "config_model_storage": c["model_storage"],
            "config_purchase_option": c["purchase_option"],
            "status": c["status"],
            "error": c["error"],
            "startup_sec": c["startup_sec"],
            "elapsed_sec": c["elapsed_sec"],
            "run_cost_usd": c["run_cost_usd"],
        })
    return buf.getvalue()
