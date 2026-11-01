"""画面から起動した設定の履歴と、名前を付けて保存した設定。同じ設定でもう一度起動できるよう、tfvars に書いた値を残す。

tfvars(terraform/tfvars/<環境ID>.tfvars.json)は環境を削除すると消えるので、履歴は別のファイル(JSON Lines)に残す。
履歴は新しい20件しか画面に出ないので、よく使う設定は名前を付けて別のファイル(JSON)に保存できる。
"""

import datetime
import json
import os
import threading
import uuid

# 画面に出す件数
DEFAULT_LIMIT = 20
# 保存した設定の名前の長さ
NAME_MAX_LENGTH = 40


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


class LaunchHistory:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def append(self, workspace, variables):
        record = {
            "id": uuid.uuid4().hex[:12],
            "launched_at": _now(),
            "workspace": workspace,
            "variables": variables,
        }
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def get(self, record_id):
        """画面に出していない古いものも探す。なければ None"""
        for r in self._read():
            if r["id"] == record_id:
                return r
        return None

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def list(self, limit=DEFAULT_LIMIT):
        """新しい順。同じ設定で何度も起動したものは、いちばん新しい1件にまとめる(環境IDの違いは見ない)"""
        seen, result = set(), []
        for r in reversed(self._read()):
            key = json.dumps(r["variables"], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            result.append(r)
            if len(result) >= limit:
                break
        return result


class LaunchPresets:
    """名前を付けて保存した設定。名前の順に並べる。同じ名前で保存すると置き換える"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return []

    def _write(self, presets):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(presets, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def list(self):
        return sorted(self._read(), key=lambda p: p["name"])

    def save(self, name, workspace, variables):
        record = {"id": uuid.uuid4().hex[:12], "name": name, "saved_at": _now(), "workspace": workspace, "variables": variables}
        with self._lock:
            self._write([p for p in self._read() if p["name"] != name] + [record])
        return record

    def delete(self, preset_id):
        """消したら True。なければ False"""
        with self._lock:
            presets = self._read()
            rest = [p for p in presets if p["id"] != preset_id]
            if len(rest) == len(presets):
                return False
            self._write(rest)
        return True
