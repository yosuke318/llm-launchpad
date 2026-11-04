"""知らせ(準備完了・自動停止の直前・スポットの中断)のテスト"""

import asyncio
import datetime
import json
import os
import stat
import sys
import urllib.error

import pytest
from fastapi.testclient import TestClient

from llm_launchpad_webui.app import create_app
from llm_launchpad_webui.notifications import Notifier, detect, message
from llm_launchpad_webui.runner import Runner

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime.datetime(2026, 11, 3, 3, 0, tzinfo=datetime.timezone.utc)


def at(minutes):
    return (NOW + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def instance(state="running", reason=None, launch_time="2026-11-03T02:00:00+00:00"):
    return {"id": "i-1", "state": state, "state_reason": reason, "launch_time": launch_time, "env_id": "qwen3-8b"}


READY = {"state": "ready", "stage": "ready", "ready_at": at(-5)}


def kinds(events):
    return [(e["kind"], e.get("reason")) for e in events]


def test_ready_is_notified_while_fresh():
    assert kinds(detect("qwen3-8b", READY, instance(), None, NOW)) == [("ready", None)]
    old = {**READY, "ready_at": at(-120)}
    assert detect("qwen3-8b", old, instance(), None, NOW) == [], "サーバーを起動し直しても、ずっと前の準備完了は知らせない"
    assert detect("qwen3-8b", READY, instance("stopped"), None, NOW) == [], "止まっていれば知らせない"
    assert detect("qwen3-8b", {**READY, "state": "running"}, instance(), None, NOW) == []


@pytest.mark.parametrize("minutes, notified", [(9, True), (10, True), (11, False), (-1, False)])
def test_idle_stop_is_notified_10_minutes_before(minutes, notified):
    status = {"state": "ready", "ready_at": at(-300), "idle_stop_at": at(minutes)}
    assert (("auto_stop_soon", "idle") in kinds(detect("qwen3-8b", status, instance(), None, NOW))) is notified


def test_max_lifetime_is_notified_before_stop():
    events = detect("qwen3-8b", {}, instance(), at(5), NOW)
    assert kinds(events) == [("auto_stop_soon", "max_lifetime")]
    assert events[0]["stop_at"] == "2026-11-03T03:05:00+00:00"
    assert detect("qwen3-8b", {}, instance("stopped"), at(5), NOW) == [], "止まっていれば知らせない"


def test_spot_interruption_is_notified():
    events = detect("qwen3-8b", {}, instance("stopped", "Server.SpotInstanceShutdown"), None, NOW)
    assert kinds(events) == [("spot_interrupted", None)]
    assert detect("qwen3-8b", {}, instance("stopped", "Client.UserInitiatedShutdown"), None, NOW) == [], "手やアイドル停止で止めたものは中断ではない"
    again = detect("qwen3-8b", {}, instance("stopped", "Server.SpotInstanceShutdown", "2026-11-03T05:00:00+00:00"), None, NOW)
    assert again[0]["key"] != events[0]["key"], "動かし直してから中断されたら、また知らせる"


def test_notifier_adds_each_key_once_and_keeps_them(tmp_path):
    path = str(tmp_path / "notifications.jsonl")
    sent = []
    n = Notifier(path, "https://hooks.slack.example/T/B/x", post=lambda url, body: sent.append((url, body)))
    events = detect("qwen3-8b", READY, instance(), None, NOW)
    assert [r["id"] for r in n.add(events)] == [1]
    assert n.add(events) == [], "同じ知らせは1回だけ"
    assert sent == [("https://hooks.slack.example/T/B/x", {"text": "llm-launchpad: 環境 qwen3-8b の準備ができました"})]

    reloaded = Notifier(path)
    assert reloaded.add(events) == [], "サーバーを起動し直しても、出した知らせは出し直さない"
    added = reloaded.add(detect("qwen3-8b", {}, instance("stopped", "Server.SpotInstanceShutdown"), None, NOW))
    assert [r["id"] for r in added] == [2]
    assert [r["id"] for r in reloaded.list(after=1)] == [2]


def test_list_reads_latest_first_then_continues_after_last_id(tmp_path):
    n = Notifier(str(tmp_path / "n.jsonl"))
    n.add([{"key": f"k{i}", "kind": "ready", "workspace": "qwen3-8b"} for i in range(1, 8)])
    assert [r["id"] for r in n.list(limit=3)] == [5, 6, 7], "初めは新しいほうから"
    assert [r["id"] for r in n.list(after=2, limit=3)] == [3, 4, 5], "続きは古いほうから。多くても取りこぼさない"
    assert [r["id"] for r in n.list(after=5, limit=3)] == [6, 7]


def test_watcher_stops_with_server(tmp_path):
    d = tmp_path / "terraform"
    d.mkdir()
    app = create_app(Runner(str(d), "terraform"), web_dist=None, aws_info=FakeAwsInfo([], {}), notify_interval_sec=3600)
    with TestClient(app):
        assert not app.state.watch_task.done()
    assert app.state.watch_task.cancelled(), "サーバーを止めたら、見に行くのも止まり終わる"


def test_slack_failure_keeps_notification(tmp_path):
    def fail(url, body):
        raise urllib.error.URLError("connection refused")

    n = Notifier(str(tmp_path / "n.jsonl"), "https://hooks.slack.example/x", post=fail)
    assert len(n.add(detect("qwen3-8b", READY, instance(), None, NOW))) == 1
    assert "Slackに送れませんでした" in n.last_error


def test_message_of_auto_stop_has_minutes():
    stop_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=8)).isoformat()
    text = message({"kind": "auto_stop_soon", "reason": "idle", "workspace": "qwen3-8b", "stop_at": stop_at})
    assert text == "llm-launchpad: 環境 qwen3-8b は、リクエストがないため約8分後に止まります"


class FakeAwsInfo:
    def __init__(self, instances, statuses):
        self.instances = instances
        self.statuses = statuses

    def project_instances(self, region):
        return [i for i in self.instances if i["region"] == region]

    def bootstrap_status(self, region, env_id):
        return self.statuses.get(env_id)


def test_check_notifications_looks_at_launched_environments(tmp_path, monkeypatch):
    d = tmp_path / "terraform"
    (d / "tfvars").mkdir(parents=True)
    for ws, region, hours in [("qwen3-8b", "ap-northeast-1", 8), ("gemma3-12b", "eu-north-1", 0), ("gone", "ap-northeast-1", 8)]:
        (d / "tfvars" / f"{ws}.tfvars.json").write_text(json.dumps({"model": "qwen3:8b", "region": region, "max_lifetime_hours": hours}))
    fake = tmp_path / "terraform-bin"
    fake.write_text(f"#!/bin/sh\nexec {sys.executable} {os.path.join(HERE, 'fake_terraform.py')} \"$@\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    stop_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setenv("FAKE_TERRAFORM_STOP_AT", stop_at)
    ready_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    aws = FakeAwsInfo(
        [{**instance(), "region": "ap-northeast-1"},
         {**instance("stopped", "Server.SpotInstanceShutdown"), "env_id": "gemma3-12b", "region": "eu-north-1"}],
        {"qwen3-8b": {"state": "ready", "ready_at": ready_at}},
    )
    app = create_app(Runner(str(d), str(fake)), web_dist=None, aws_info=aws)
    with TestClient(app) as c:
        added = asyncio.run(app.state.check_notifications())
        assert sorted((e["workspace"], e["kind"], e.get("reason")) for e in added) == [
            ("gemma3-12b", "spot_interrupted", None),
            ("qwen3-8b", "auto_stop_soon", "max_lifetime"),
            ("qwen3-8b", "ready", None),
        ], "インスタンスのない環境(gone)は見ない"
        res = c.get("/api/notifications", params={"after": 1}).json()
        assert [n["id"] for n in res["notifications"]] == [2, 3]
        assert res["slack"] is False
