"""画面から起動した設定の履歴。同じ設定でもう一度起動できるよう、tfvars に書いた値を JSON Lines のファイルに足していく。

tfvars(terraform/tfvars/<環境ID>.tfvars.json)は環境を削除すると消えるので、履歴は別のファイルに残す。
"""

import datetime
import json
import os
import threading
import uuid

# 画面に出す件数
DEFAULT_LIMIT = 20


class LaunchHistory:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def append(self, workspace, variables):
        record = {
            "id": uuid.uuid4().hex[:12],
            "launched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "workspace": workspace,
            "variables": variables,
        }
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def list(self, limit=DEFAULT_LIMIT):
        """新しい順。同じ設定で何度も起動したものは、いちばん新しい1件にまとめる(環境IDの違いは見ない)"""
        try:
            with open(self.path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
        seen, result = set(), []
        for r in reversed(records):
            key = json.dumps(r["variables"], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            result.append(r)
            if len(result) >= limit:
                break
        return result
