"""ベンチマーク。インスタンスの launchpad.py bench を SSM の send-command で動かし、結果に料金と VRAM の見積もりを足して保存する。

    TTFT と tok/s は、インスタンスの中から Ollama に送って測る(ネットワークの遅れを含まない)
    VRAM は nvidia-smi の使用量の合計。見積もり(sizing.py)との差も出す
    $/100万トークン = 1時間あたりの料金 ÷ (出力の tok/s × 3600) × 100万。1つずつ順に送ったときの値

結果は JSON Lines のファイルに1行ずつ足していく。CSV と JSON で出せる(記事の表に使う)。
"""

import csv
import io
import json
import os
import shlex
import threading
import uuid

from .messages import MessageError

# インスタンスに置いた補助スクリプト(terraform/modules/bootstrap/templates/user_data.sh.tftpl)
BENCH_COMMAND = "python3 /opt/llm-launchpad/launchpad.py bench {model} --runs {runs}"
DEFAULT_RUNS = 3
MAX_RUNS = 10

# CSV に出す列。プロンプトごとの結果(results)は JSON にだけ入れる
CSV_COLUMNS = [
    "id", "measured_at", "workspace", "model", "region", "availability_zone", "instance_type", "gpu", "purchase_option",
    "model_storage", "context_length", "kv_cache_type", "runs", "num_predict",
    "ttft_sec", "first_response_sec", "thinking", "output_tokens_per_sec", "prompt_tokens_per_sec", "warmup_load_sec",
    "vram_estimate_gib", "vram_measured_gib", "vram_diff_gib", "vram_total_gib",
    "hourly_usd", "usd_per_million_tokens",
]

GIB_PER_MIB = 1 / 1024


class BenchmarkError(MessageError):
    pass


def bench_command(model, runs):
    return BENCH_COMMAND.format(model=shlex.quote(model), runs=int(runs))


def parse_output(stdout):
    """launchpad.py bench は、結果の JSON を標準出力の最後の行に出す"""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    raise BenchmarkError("bench.no_output")


def usd_per_million_tokens(hourly_usd, tokens_per_sec):
    if not hourly_usd or not tokens_per_sec:
        return None
    return round(hourly_usd / (tokens_per_sec * 3600) * 1_000_000, 4)


def summarize(raw, workspace, tfvars, instance, hourly_usd, vram_estimate_gib):
    """インスタンスで測った結果(raw)に、環境の設定、料金、VRAM の見積もりを足して、保存する1件にする"""
    tfvars = tfvars or {}
    gpus = raw.get("gpus") or []
    measured = round(sum(g["memory_used_mib"] for g in gpus) * GIB_PER_MIB, 2) if gpus else None
    total = round(sum(g["memory_total_mib"] for g in gpus) * GIB_PER_MIB, 2) if gpus else None
    return {
        "id": uuid.uuid4().hex[:12],
        "measured_at": raw.get("measured_at"),
        "workspace": workspace,
        "model": raw.get("model"),
        "region": instance.get("region") or tfvars.get("region"),
        "availability_zone": instance.get("availability_zone"),
        "instance_type": instance.get("instance_type"),
        "gpu": " + ".join(g["name"] for g in gpus) or None,
        "purchase_option": instance.get("purchase_option"),
        "model_storage": tfvars.get("model_storage"),
        "context_length": tfvars.get("context_length") or (raw.get("loaded") or {}).get("context_length"),
        "kv_cache_type": tfvars.get("kv_cache_type"),
        "runs": raw.get("runs"),
        "num_predict": raw.get("num_predict"),
        "ttft_sec": raw.get("ttft_sec"),
        # 考えてから答えるモデルで、答えの最初のトークンが届くまで(考えている時間を含む)
        "first_response_sec": raw.get("first_response_sec"),
        "thinking": raw.get("thinking"),
        "output_tokens_per_sec": raw.get("output_tokens_per_sec"),
        "prompt_tokens_per_sec": raw.get("prompt_tokens_per_sec"),
        "warmup_load_sec": raw.get("warmup_load_sec"),
        "vram_estimate_gib": None if vram_estimate_gib is None else round(vram_estimate_gib, 2),
        "vram_measured_gib": measured,
        "vram_diff_gib": None if measured is None or vram_estimate_gib is None else round(measured - vram_estimate_gib, 2),
        "vram_total_gib": total,
        "hourly_usd": hourly_usd,
        "usd_per_million_tokens": usd_per_million_tokens(hourly_usd, raw.get("output_tokens_per_sec")),
        "results": raw.get("results", []),
        "loaded": raw.get("loaded"),
        "gpus": gpus,
    }


class BenchmarkStore:
    """測った結果の履歴。JSON Lines のファイルに足していく"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def append(self, record):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def list(self):
        """新しい順"""
        try:
            with open(self.path, encoding="utf-8") as f:
                records = [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []
        return list(reversed(records))

    def csv(self):
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for r in self.list():
            writer.writerow(r)
        return buf.getvalue()
