"""起動した環境の知らせ。準備ができたとき、自動停止の直前、スポットが中断されたときに出す。

サーバーが一定の間隔で、画面から起動した環境(tfvars を書いたもの)のインスタンスと起動処理の状態を見て、知らせることを見つける。
見つけた知らせはファイル(JSON Lines)に足し、画面がそれを読んでデスクトップ通知を出す。Slack の Webhook を指定していれば、そこにも送る。
同じ知らせ(key が同じもの)は1回だけ出す。
"""

import datetime
import json
import os
import threading
import urllib.error
import urllib.request

from .environments import parse_time
from .messages import t

# 自動停止の何秒前に知らせるか。アイドル停止はインスタンスが5分ごとに確かめるので、それより長くする
WARN_BEFORE_SEC = 10 * 60
# サーバーを起動し直したときに、ずっと前の準備完了を知らせ直さない
READY_FRESH_SEC = 60 * 60
# スポットの中断で止まったときの StateReason のコード
SPOT_INTERRUPTION_REASONS = ("Server.SpotInstanceShutdown", "Server.SpotInstanceTermination")
# 画面に返す件数
DEFAULT_LIMIT = 50


def detect(workspace, status, instance, lifetime_stop_at, now):
    """知らせることの一覧。status はインスタンスが書く起動処理の状態、instance は AWS で見たインスタンス(なければ None)、
    lifetime_stop_at は最大稼働時間で止まる時刻(terraform output の auto_stop.stop_at。なければ None)"""
    status = status or {}
    running = instance is not None and instance["state"] == "running"
    events = []

    ready_at = parse_time(status.get("ready_at"))
    if running and status.get("state") == "ready" and ready_at and (now - ready_at).total_seconds() <= READY_FRESH_SEC:
        events.append({"key": f"{workspace}:ready:{status['ready_at']}", "kind": "ready", "workspace": workspace})

    if running:
        # 最大稼働時間はインスタンスの外(EventBridge Scheduler)が、アイドル停止はインスタンスの中が止める
        for reason, at in (("max_lifetime", lifetime_stop_at), ("idle", status.get("idle_stop_at"))):
            stop_at = parse_time(at)
            if stop_at and 0 < (stop_at - now).total_seconds() <= WARN_BEFORE_SEC:
                events.append({"key": f"{workspace}:auto_stop_soon:{reason}:{at}", "kind": "auto_stop_soon",
                               "workspace": workspace, "reason": reason, "stop_at": stop_at.isoformat()})

    if instance is not None and instance["state"] in ("stopping", "stopped") and instance.get("state_reason") in SPOT_INTERRUPTION_REASONS:
        # 中断されて止まり、また動いてから中断されたときも知らせるよう、動き出した時刻を key に入れる
        events.append({"key": f"{workspace}:spot_interrupted:{instance['launch_time']}", "kind": "spot_interrupted",
                       "workspace": workspace})
    return events


def message(event):
    """Slack に送る文。画面は kind から自分の言語で文を作る"""
    if event["kind"] == "auto_stop_soon":
        stop_at = datetime.datetime.fromisoformat(event["stop_at"])
        minutes = max(1, round((stop_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds() / 60))
        return t(f"notify.auto_stop_soon.{event['reason']}", workspace=event["workspace"], minutes=minutes)
    return t(f"notify.{event['kind']}", workspace=event["workspace"])


def post_json(url, body, timeout=10):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        res.read()


class Notifier:
    """出した知らせ。id は1からの通し番号で、画面は最後に見た id より後のものを読む"""

    def __init__(self, path, slack_webhook_url=None, post=post_json):
        self.path = path
        self.slack_webhook_url = slack_webhook_url
        self.post = post
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self.records = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            self.records = []
        self.keys = {r["key"] for r in self.records}
        # Slack に送れなかったときのエラー。画面に出す
        self.last_error = None

    def add(self, events):
        """まだ出していない知らせを足し、Slack にも送る。足したものを返す"""
        added = []
        with self._lock:
            for event in events:
                if event["key"] in self.keys:
                    continue
                record = {"id": (self.records[-1]["id"] + 1) if self.records else 1,
                          "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), **event}
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.records.append(record)
                self.keys.add(event["key"])
                added.append(record)
        for record in added:
            self.send_slack(record)
        return added

    def send_slack(self, record):
        if not self.slack_webhook_url:
            return
        try:
            self.post(self.slack_webhook_url, {"text": message(record)})
            self.last_error = None
        except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError) as e:
            # 送れなくても知らせは残す(画面のデスクトップ通知は出る)
            self.last_error = t("notify.slack_failed", error=e)

    def list(self, after=0, limit=DEFAULT_LIMIT):
        """after より後の知らせ。古い順"""
        return [r for r in self.records if r["id"] > after][-limit:]
