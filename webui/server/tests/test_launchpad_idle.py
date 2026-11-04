"""アイドル停止(terraform/modules/bootstrap/files/launchpad.py の idle-check)のテスト。journalctl と /proc/uptime は偽物にする"""

import calendar
import importlib.util
import os
import subprocess
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHPAD = os.path.join(HERE, "..", "..", "..", "terraform", "modules", "bootstrap", "files", "launchpad.py")

NOW = 1_800_000_000.0


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


@pytest.fixture
def launchpad(monkeypatch, tmp_path):
    monkeypatch.setenv("LAUNCHPAD_STATE_FILE", str(tmp_path / "status.json"))
    monkeypatch.setenv("LAUNCHPAD_UPTIME_FILE", str(tmp_path / "uptime"))
    monkeypatch.delenv("LAUNCHPAD_STATUS_PARAMETER", raising=False)
    spec = importlib.util.spec_from_file_location("launchpad", LAUNCHPAD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "time", lambda: NOW)
    module.commands = []
    module.journal = ""

    def run(cmd, **kwargs):
        module.commands.append(cmd)
        out = module.journal if cmd[0] == "journalctl" else ""
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    module.set_uptime = lambda sec: (tmp_path / "uptime").write_text(f"{sec} 0\n")
    return module


def ready(launchpad, ready_at):
    launchpad.save_state({"stage": "ready", "state": "ready", "ready_at": iso(ready_at), "updated_at": iso(ready_at)})


def gin(ts, path):
    return f'{ts:.6f} ip-10-0-0-1 ollama[123]: [GIN] 2027/01/15 - 08:00:00 | 200 | 1.2s | 127.0.0.1 | POST     "{path}"\n'


def test_writes_idle_stop_at_from_last_request(launchpad):
    launchpad.set_uptime(3600)
    ready(launchpad, NOW - 3000)
    launchpad.journal = gin(NOW - 600, "/api/chat") + gin(NOW - 60, "/api/tags")
    launchpad.idle_check(30)
    state = launchpad.load_state()
    # 状態の確認(/api/tags)は数えず、最後の /api/chat から30分
    assert state["idle_stop_at"] == iso(NOW - 600 + 1800)
    assert state["updated_at"] == iso(NOW - 3000), "updated_at は変えない(止める時刻の数え始めがずれないように)"
    assert not any(c[:2] == ["systemctl", "poweroff"] for c in launchpad.commands)


def test_counts_from_ready_when_no_request(launchpad):
    launchpad.set_uptime(3600)
    ready(launchpad, NOW - 600)
    launchpad.idle_check(30)
    assert launchpad.load_state()["idle_stop_at"] == iso(NOW - 600 + 1800)


def test_counts_from_boot_after_restart(launchpad):
    launchpad.set_uptime(300)
    ready(launchpad, NOW - 86400)
    launchpad.idle_check(30)
    assert launchpad.load_state()["idle_stop_at"] == iso(NOW - 300 + 1800), "停止から再開したら、再開から数える"


def test_stops_when_idle_for_window(launchpad):
    launchpad.set_uptime(7200)
    ready(launchpad, NOW - 7200)
    launchpad.idle_check(30)
    state = launchpad.load_state()
    assert state["idle_stop_at"] == iso(NOW - 7200 + 1800)
    assert state["auto_stop"]["reason"] == "idle"
    assert ["systemctl", "poweroff"] in launchpad.commands


def test_keeps_running_while_bootstrap_runs(launchpad):
    launchpad.save_state({"stage": "pulling_model", "state": "running", "updated_at": iso(NOW)})
    launchpad.idle_check(30)
    assert "idle_stop_at" not in launchpad.load_state()
