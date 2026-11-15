#!/usr/bin/env python3
"""GPUインスタンスの起動処理の進み具合を SSM Parameter Store に書き、Ollama でモデルを取得・確認する。

user data(user_data.sh.tftpl)から呼ぶ。標準ライブラリと AWS CLI だけを使う。

    launchpad.py status <stage> [--state running|ready|failed] [--error MSG] [--log PATH]
    launchpad.py wait-server [--timeout SEC]
    launchpad.py track-download <dir> <expected_bytes>
    launchpad.py pull <model>
    launchpad.py check-local <model>
    launchpad.py verify <model>
    launchpad.py restore-s3 <model> <bucket> <models_dir> [--offline]
    launchpad.py save-s3 <model> <bucket> <models_dir>
    launchpad.py mount-ebs <volume_id> <mount_point>
    launchpad.py idle-check --minutes N [--dry-run]
    launchpad.py bench <model> [--runs N] [--num-predict N]

設定は環境変数で渡す。
    LAUNCHPAD_REGION             パラメータのあるリージョン
    LAUNCHPAD_STATUS_PARAMETER   状態を書くパラメータ名。空なら手元のファイルにだけ書く
    LAUNCHPAD_STATE_FILE         手元の状態ファイル(既定 /var/lib/llm-launchpad/status.json)
    LAUNCHPAD_FSTAB              EBSボリュームを書く fstab(既定 /etc/fstab)
    LAUNCHPAD_UPTIME_FILE        起動からの秒数を読むファイル(既定 /proc/uptime)
    OLLAMA_URL                   推論エンジンの API(既定 http://127.0.0.1:11434。vLLM も同じポートで動かす)
    LAUNCHPAD_ENGINE             推論エンジン(ollama / vllm)。なければ LAUNCHPAD_ENGINE_FILE を読む
    LAUNCHPAD_ENGINE_FILE        起動処理が推論エンジンを書いたファイル(既定 /var/lib/llm-launchpad/engine)

推論エンジンが vLLM のときは、Ollama の API(/api/...)の代わりに OpenAI 互換 API(/v1/...)を使う。
"""

import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REGION = os.environ.get("LAUNCHPAD_REGION", "")
STATUS_PARAMETER = os.environ.get("LAUNCHPAD_STATUS_PARAMETER", "")
STATE_FILE = os.environ.get("LAUNCHPAD_STATE_FILE", "/var/lib/llm-launchpad/status.json")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
FSTAB = os.environ.get("LAUNCHPAD_FSTAB", "/etc/fstab")
UPTIME_FILE = os.environ.get("LAUNCHPAD_UPTIME_FILE", "/proc/uptime")
ENGINE_FILE = os.environ.get("LAUNCHPAD_ENGINE_FILE", "/var/lib/llm-launchpad/engine")


def engine():
    """推論エンジン。SSM の send-command で動かすとき(計測)は環境変数がないので、起動処理が書いたファイルを読む"""
    value = os.environ.get("LAUNCHPAD_ENGINE")
    if not value:
        try:
            with open(ENGINE_FILE, encoding="utf-8") as f:
                value = f.read().strip()
        except FileNotFoundError:
            value = ""
    return value or "ollama"

# 取得中の進み具合は、この秒数か、この割合(%)だけ進むごとに書く。PutParameter の回数を抑えるため
PUBLISH_INTERVAL_SEC = 10
PUBLISH_STEP_PERCENT = 5


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log(msg):
    print(f"[launchpad {now()}] {msg}", flush=True)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

    if not STATUS_PARAMETER:
        return

    # 書けなくても起動処理は止めない(状態は手元のファイルにも残る)
    cmd = [
        "aws", "ssm", "put-parameter",
        "--name", STATUS_PARAMETER,
        "--type", "String",
        "--overwrite",
        "--value", json.dumps(state, ensure_ascii=False, separators=(",", ":")),
    ]
    if REGION:
        cmd += ["--region", REGION]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(f"状態をパラメータに書けませんでした: {result.stderr.strip()}")


def set_status(stage, state="running", **fields):
    s = load_state()
    ts = now()
    s.setdefault("started_at", ts)
    s.setdefault("stages", {})

    # 段階ごとの開始時刻を残す(コールドスタートの計測に使う)。同じ段階を何度書いても最初の時刻のまま
    s["stages"].setdefault(stage, ts)
    if state == "ready":
        s["ready_at"] = s.get("ready_at", ts)

    s["stage"] = stage
    s["state"] = state
    s["updated_at"] = ts

    if state != "failed":
        s.pop("error", None)
    elif s.get("state") == "failed" and s.get("error"):
        # Python 側で書いた具体的なエラーを、あとから bash のトラップが行番号だけのエラーで上書きしない
        fields["error"] = None
    if stage != "pulling_model":
        s.pop("pull", None)

    for key, value in fields.items():
        if value is not None:
            s[key] = value

    save_state(s)
    return s


def request(path, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        OLLAMA_URL + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    return urllib.request.urlopen(req, timeout=timeout)


def wait_server(timeout_sec=300):
    if engine() == "vllm":
        return wait_vllm(timeout_sec)
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            with request("/api/version", timeout=5) as res:
                version = json.load(res).get("version", "")
                log(f"Ollama {version} が応答しました")
                s = load_state()
                set_status(s.get("stage", "installing_ollama"), s.get("state", "running"), ollama_version=version)
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError):
            if time.monotonic() > deadline:
                raise RuntimeError(f"Ollama が {timeout_sec} 秒以内に応答しませんでした")
            time.sleep(2)


def pull(model):
    """/api/pull の進み具合(レイヤーごとの total / completed)を合計して割合を出す"""
    layers = {}
    last_publish = 0.0
    last_percent = -PUBLISH_STEP_PERCENT

    with request("/api/pull", {"model": model, "stream": True}, timeout=600) as res:
        for line in res:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if "error" in event:
                raise RuntimeError(f"モデルの取得に失敗しました: {event['error']}")

            digest = event.get("digest")
            if digest and event.get("total"):
                layers[digest] = (event.get("completed", 0), event["total"])

            total = sum(t for _, t in layers.values())
            completed = sum(c for c, _ in layers.values())
            percent = round(completed * 100 / total, 1) if total else 0.0

            elapsed = time.monotonic() - last_publish
            if percent - last_percent >= PUBLISH_STEP_PERCENT or elapsed >= PUBLISH_INTERVAL_SEC:
                set_status("pulling_model", pull={
                    "percent": percent,
                    "completed_bytes": completed,
                    "total_bytes": total,
                    "message": event.get("status", ""),
                })
                last_publish = time.monotonic()
                last_percent = percent

            if event.get("status") == "success":
                set_status("pulling_model", pull={
                    "percent": 100.0,
                    "completed_bytes": total,
                    "total_bytes": total,
                    "message": "success",
                })
                log(f"{model} を取得しました({total} バイト)")
                return

    raise RuntimeError("モデルの取得が完了の通知なしで終わりました")


def check_local(model):
    """閉じたネットワーク用。Ollama library に問い合わせず、モデルが手元にあるかだけを確かめる"""
    try:
        with request("/api/show", {"model": model}) as res:
            json.load(res)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError(
                f"{model} が手元にありません。閉じたネットワークでは Ollama library から取得できないため、"
                "インターネットのある環境で S3 か EBS ボリュームに入れておいてください"
            ) from e
        raise
    set_status("pulling_model", pull={"percent": 100.0, "message": "already present"})
    log(f"{model} は手元にあります")


def wait_vllm(timeout_sec):
    """vLLM は、モデルを GPU に載せて CUDA グラフを用意し終えると /health が 200 を返す。コンテナが止まっていたら失敗にする"""
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            with request("/health", timeout=5):
                pass
            with request("/version", timeout=5) as res:
                version = json.load(res).get("version", "")
            log(f"vLLM {version} が応答しました")
            s = load_state()
            set_status(s.get("stage", "verifying"), s.get("state", "running"), vllm_version=version)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError):
            pass
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", "vllm"], capture_output=True, text=True).stdout.strip()
        if state in ("exited", "dead"):
            tail = subprocess.run(["docker", "logs", "--tail", "20", "vllm"], capture_output=True, text=True)
            raise RuntimeError(f"vLLM のコンテナが止まりました: {(tail.stdout + tail.stderr).strip()[-1500:]}")
        if time.monotonic() > deadline:
            raise RuntimeError(f"vLLM が {timeout_sec} 秒以内に応答しませんでした")
        time.sleep(5)


def track_download(directory, expected_bytes, interval_sec=PUBLISH_INTERVAL_SEC):
    """Hugging Face からの取得の進み具合を、キャッシュの大きさから出して書く。起動処理がバックグラウンドで動かし、
    取得が終わると止める。ほかの段階に進んだか失敗したら、状態を上書きしないよう自分で止まる"""
    while True:
        s = load_state()
        if s.get("stage") != "pulling_model" or s.get("state") != "running":
            return
        completed = 0
        for root, _, files in os.walk(directory):
            for name in files:
                try:
                    completed += os.lstat(os.path.join(root, name)).st_size
                except FileNotFoundError:
                    pass
        # 重みの大きさは見積もりなので、取得が終わるまでは 100% にしない
        percent = min(round(completed * 100 / expected_bytes, 1), 99.0) if expected_bytes else 0.0
        set_status("pulling_model", pull={"percent": percent, "completed_bytes": completed, "total_bytes": expected_bytes, "message": "downloading"})
        time.sleep(interval_sec)


def gpu_memory():
    """nvidia-smi で GPU ごとのメモリを取る。見積もりと実測の比較に使う"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        log(f"nvidia-smi を実行できませんでした: {e}")
        return None

    gpus = []
    for row in out.strip().splitlines():
        index, name, used, total = [c.strip() for c in row.split(",")]
        gpus.append({"index": int(index), "name": name, "memory_used_mib": int(used), "memory_total_mib": int(total)})
    return gpus


def verify(model):
    """短いプロンプトを1回送り、応答が返るかを確かめる。大きいモデルはGPUへの読み込みに数分かかる"""
    started = time.monotonic()
    if engine() == "vllm":
        with request("/v1/completions", {"model": model, "prompt": "Reply with OK.", "max_tokens": 8}, timeout=1800) as res:
            result = json.load(res)
        choices = result.get("choices") or []
        if not choices or "text" not in choices[0]:
            raise RuntimeError(f"応答の形式が想定と違います: {result}")
        result = {"response": choices[0]["text"]}
    else:
        body = {"model": model, "prompt": "Reply with OK.", "stream": False, "options": {"num_predict": 8}}
        with request("/api/generate", body, timeout=1800) as res:
            result = json.load(res)
        if "response" not in result:
            raise RuntimeError(f"応答の形式が想定と違います: {result}")

    loaded = loaded_model(model)

    first_response_sec = round(time.monotonic() - started, 1)
    log(f"{model} が {first_response_sec} 秒で応答しました: {result['response']!r}")
    set_status("verifying", verify={
        "first_response_sec": first_response_sec,
        "loaded": loaded,
        "gpus": gpu_memory(),
    })


# ベンチマークで流すプロンプト。毎回同じ結果になるよう、温度 0 と seed を決めて送る
BENCH_PROMPTS = [
    {"id": "short", "prompt": "Explain what a GPU is in three sentences."},
    {"id": "long", "prompt": "Write a detailed explanation of how HTTP caching works, covering Cache-Control, ETag, and Last-Modified."},
    {"id": "code", "prompt": "Write a Python function that returns the n-th Fibonacci number using memoization, with a short docstring."},
]
BENCH_NUM_PREDICT = 256


def generate_timed(model, prompt, num_predict):
    """/api/generate をストリームで送り、最初のトークンまでの時間(TTFT)と、Ollama が数えたトークン数と時間を返す"""
    if engine() == "vllm":
        return generate_timed_vllm(model, prompt, num_predict)
    body = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"num_predict": num_predict, "temperature": 0, "seed": 0},
    }
    started = time.monotonic()
    ttft = None
    first_response = None
    thinking = False
    final = None
    with request("/api/generate", body, timeout=1800) as res:
        for line in res:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if "error" in event:
                raise RuntimeError(f"生成に失敗しました: {event['error']}")
            # 考えてから答えるモデル(qwen3、gpt-oss など)は、考えている間の出力を thinking に入れて返す。
            # TTFT はどちらでも最初のトークンが届くまで、答え(response)の最初のトークンまでは別に残す
            if event.get("thinking"):
                thinking = True
            if ttft is None and (event.get("response") or event.get("thinking")):
                ttft = time.monotonic() - started
            if first_response is None and event.get("response"):
                first_response = time.monotonic() - started
            if event.get("done"):
                final = event
    if final is None:
        raise RuntimeError("生成が完了の通知なしで終わりました")
    eval_sec = final.get("eval_duration", 0) / 1e9
    prompt_eval_sec = final.get("prompt_eval_duration", 0) / 1e9
    return {
        "ttft_sec": None if ttft is None else round(ttft, 3),
        "first_response_sec": None if first_response is None else round(first_response, 3),
        "thinking": thinking,
        "total_sec": round(time.monotonic() - started, 3),
        "prompt_tokens": final.get("prompt_eval_count", 0),
        "prompt_tokens_per_sec": round(final.get("prompt_eval_count", 0) / prompt_eval_sec, 2) if prompt_eval_sec else None,
        "output_tokens": final.get("eval_count", 0),
        "output_tokens_per_sec": round(final.get("eval_count", 0) / eval_sec, 2) if eval_sec else None,
        "load_sec": round(final.get("load_duration", 0) / 1e9, 3),
    }


def generate_timed_vllm(model, prompt, num_predict):
    """vLLM の /v1/chat/completions をストリームで送る。Ollama と同じく、温度 0、seed 0、出力は num_predict トークンまで。
    出力の tok/s は「出力トークン数 / 最初のトークンから最後までの時間」(Ollama の eval_duration に近いもの)。
    vLLM は読み込みの時間とプロンプトの処理時間を別に返さないので、prompt_tokens_per_sec と load_sec は出さない"""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": num_predict,
        "temperature": 0,
        "seed": 0,
    }
    started = time.monotonic()
    ttft = None
    first_response = None
    thinking = False
    usage = None
    last_token = None
    with request("/v1/chat/completions", body, timeout=1800) as res:
        for raw in res:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            if "error" in event:
                raise RuntimeError(f"生成に失敗しました: {event['error']}")
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                # 考えてから答えるモデルは、考えている間の出力を reasoning_content に入れて返すことがある
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                content = delta.get("content")
                if reasoning:
                    thinking = True
                if reasoning or content:
                    last_token = time.monotonic()
                    if ttft is None:
                        ttft = last_token - started
                if content and first_response is None:
                    first_response = time.monotonic() - started
    if usage is None:
        raise RuntimeError("生成がトークン数の通知なしで終わりました")
    output_tokens = usage.get("completion_tokens", 0)
    decode_sec = (last_token - started - ttft) if (last_token is not None and ttft is not None) else 0
    return {
        "ttft_sec": None if ttft is None else round(ttft, 3),
        "first_response_sec": None if first_response is None else round(first_response, 3),
        "thinking": thinking,
        "total_sec": round(time.monotonic() - started, 3),
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "prompt_tokens_per_sec": None,
        "output_tokens": output_tokens,
        "output_tokens_per_sec": round((output_tokens - 1) / decode_sec, 2) if decode_sec > 0 and output_tokens > 1 else None,
        "load_sec": None,
    }


def loaded_model(model):
    """/api/ps にある、そのモデルの大きさと、そのうち GPU に載った大きさ。vLLM では、使えるコンテキスト長(max_model_len)"""
    if engine() == "vllm":
        try:
            with request("/v1/models") as res:
                for m in json.load(res).get("data", []):
                    if m.get("id") == model:
                        return {"context_length": m.get("max_model_len")}
        except (urllib.error.URLError, json.JSONDecodeError):
            pass
        return {}
    try:
        with request("/api/ps") as res:
            for m in json.load(res).get("models", []):
                if m.get("name") == model or m.get("model") == model:
                    return {"size_bytes": m.get("size"), "size_vram_bytes": m.get("size_vram"), "context_length": m.get("context_length")}
    except (urllib.error.URLError, json.JSONDecodeError):
        pass
    return {}


def bench(model, runs=1, num_predict=BENCH_NUM_PREDICT):
    """決まったプロンプトを流して、TTFT と tok/s を測る。最初に1回送ってモデルを GPU に載せ、その分は数えない。
    結果は JSON で標準出力の最後の行に出す(WebUI は SSM の send-command で呼び、その行を読む)"""
    warmup = generate_timed(model, "Reply with OK.", 8)
    results = []
    for run in range(runs):
        for p in BENCH_PROMPTS:
            results.append({"prompt_id": p["id"], "run": run + 1, **generate_timed(model, p["prompt"], num_predict)})

    def mean(key):
        values = [r[key] for r in results if r[key] is not None]
        return round(sum(values) / len(values), 3) if values else None

    out = {
        "model": model,
        "engine": engine(),
        "measured_at": now(),
        "num_predict": num_predict,
        "runs": runs,
        "warmup_load_sec": warmup["load_sec"],
        "ttft_sec": mean("ttft_sec"),
        "first_response_sec": mean("first_response_sec"),
        "thinking": any(r["thinking"] for r in results),
        "output_tokens_per_sec": mean("output_tokens_per_sec"),
        "prompt_tokens_per_sec": mean("prompt_tokens_per_sec"),
        "results": results,
        "loaded": loaded_model(model),
        "gpus": gpu_memory(),
    }
    print(json.dumps(out, ensure_ascii=False), flush=True)
    return out


S3_PREFIX = "ollama/models"


def aws(*args, check=True):
    cmd = ["aws", *args]
    if REGION:
        cmd += ["--region", REGION]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"aws {' '.join(args)} が失敗しました: {result.stderr.strip()}")
    return result


def manifest_path(model):
    """Ollama の models ディレクトリでの manifest の場所。qwen3:8b なら manifests/registry.ollama.ai/library/qwen3/8b"""
    name, _, tag = model.rpartition(":") if ":" in model.rsplit("/", 1)[-1] else (model, "", "latest")
    parts = name.split("/")
    if "." in parts[0] and len(parts) > 1:
        host, rest = parts[0], parts[1:]
    else:
        host, rest = "registry.ollama.ai", (["library"] if len(parts) == 1 else []) + parts
    return "/".join(["manifests", host, *rest, tag])


def blob_paths(manifest):
    """manifest が参照する blob の場所。digest の sha256:xxx は blobs/sha256-xxx に置かれる"""
    digests = [manifest["config"]["digest"]] + [layer["digest"] for layer in manifest.get("layers", [])]
    return ["blobs/" + d.replace(":", "-") for d in digests]


def record_cache(**fields):
    s = load_state()
    cache = s.get("cache", {})
    cache.update(fields)
    set_status(s.get("stage", "unknown"), s.get("state", "running"), cache=cache)


def restore_s3(model, bucket, models_dir, offline=False):
    """S3 に manifest があれば、manifest と blob を models ディレクトリにコピーする。
    なければ何もしない。offline(閉じたネットワーク)では、あとで取得できないため失敗にする"""
    key = f"{S3_PREFIX}/{manifest_path(model)}"
    if aws("s3api", "head-object", "--bucket", bucket, "--key", key, check=False).returncode != 0:
        if offline:
            raise RuntimeError(
                f"s3://{bucket}/{key} がありません。閉じたネットワークでは Ollama library から取得できないため、"
                "インターネットのある環境でモデルを S3 に保存しておいてください"
            )
        log(f"S3 に {model} はありません。Ollama library から取得し、あとで S3 に保存します")
        record_cache(hit=False, bucket=bucket)
        return

    started = time.monotonic()
    local_manifest = os.path.join(models_dir, manifest_path(model))
    os.makedirs(os.path.dirname(local_manifest), exist_ok=True)
    aws("s3", "cp", f"s3://{bucket}/{key}", local_manifest, "--only-show-errors")
    with open(local_manifest, encoding="utf-8") as f:
        blobs = blob_paths(json.load(f))

    total = 0
    for blob in blobs:
        local = os.path.join(models_dir, blob)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        aws("s3", "cp", f"s3://{bucket}/{S3_PREFIX}/{blob}", local, "--only-show-errors")
        total += os.path.getsize(local)

    sec = round(time.monotonic() - started, 1)
    log(f"S3 から {model} をコピーしました({total} バイト、{sec} 秒)")
    record_cache(hit=True, bucket=bucket, restore_sec=sec, restored_bytes=total)


def save_s3(model, bucket, models_dir):
    """S3 になかったモデルを保存する。blob を先に、manifest を最後に置く(manifest があれば揃っているとみなすため)"""
    if load_state().get("cache", {}).get("hit"):
        return

    started = time.monotonic()
    local_manifest = os.path.join(models_dir, manifest_path(model))
    with open(local_manifest, encoding="utf-8") as f:
        blobs = blob_paths(json.load(f))

    total = 0
    for blob in blobs:
        key = f"{S3_PREFIX}/{blob}"
        # 別のモデルと共有している blob は保存済みのことがある
        if aws("s3api", "head-object", "--bucket", bucket, "--key", key, check=False).returncode == 0:
            continue
        local = os.path.join(models_dir, blob)
        aws("s3", "cp", local, f"s3://{bucket}/{key}", "--only-show-errors")
        total += os.path.getsize(local)
    aws("s3", "cp", local_manifest, f"s3://{bucket}/{S3_PREFIX}/{manifest_path(model)}", "--only-show-errors")

    sec = round(time.monotonic() - started, 1)
    log(f"{model} を S3 に保存しました({total} バイト、{sec} 秒)")
    record_cache(saved_at=now(), save_sec=sec, saved_bytes=total)


def find_volume_device(volume_id, timeout_sec=600):
    """Nitro のインスタンスでは EBS は NVMe として見え、by-id にボリュームID(ハイフンなし)が入る"""
    path = "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_" + volume_id.replace("-", "")
    deadline = time.monotonic() + timeout_sec
    while not os.path.exists(path):
        # ボリュームはインスタンスの起動後に付けられるため、少し待つことがある
        if time.monotonic() > deadline:
            raise RuntimeError(f"EBSボリューム {volume_id} が {timeout_sec} 秒以内に付きませんでした")
        time.sleep(5)
    return os.path.realpath(path)


def mount_ebs(volume_id, mount_point):
    device = find_volume_device(volume_id)
    fs_type = subprocess.run(["blkid", "-o", "value", "-s", "TYPE", device], capture_output=True, text=True).stdout.strip()
    formatted = False
    if not fs_type:
        # 初めて使うボリュームだけ初期化する。中身があるボリュームは初期化しない
        subprocess.run(["mkfs.ext4", "-q", "-L", "llm-models", device], check=True)
        formatted = True

    os.makedirs(mount_point, exist_ok=True)
    if subprocess.run(["mountpoint", "-q", mount_point]).returncode != 0:
        subprocess.run(["mount", device, mount_point], check=True)

    # 停止・再開のあとも付くよう fstab に書く。付いていなくても起動を止めない(nofail)
    uuid = subprocess.run(["blkid", "-o", "value", "-s", "UUID", device], capture_output=True, text=True, check=True).stdout.strip()
    entry = f"UUID={uuid} {mount_point} ext4 defaults,nofail 0 2\n"
    with open(FSTAB, encoding="utf-8") as f:
        has_entry = any(line.split()[:2] == [f"UUID={uuid}", mount_point] for line in f if line.strip())
    if not has_entry:
        with open(FSTAB, "a", encoding="utf-8") as f:
            f.write(entry)

    log(f"EBSボリューム {volume_id}({device})を {mount_point} に付けました(初期化: {formatted})")
    record_cache(volume_id=volume_id, formatted=formatted)


# 起動処理や状態の確認で使うリクエスト。使っている人のリクエストとは数えない
IGNORED_PATHS = ("/api/version", "/api/ps", "/api/tags")


def parse_time(ts):
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def ollama_request_times(minutes):
    """Ollama のログ(journald)にある、直近 minutes 分のリクエストの時刻(エポック秒)。
    ログは「1730000000.123456 ホスト ollama[123]: [GIN] ... | POST "/api/chat"」の形(-o short-unix)"""
    out = subprocess.run(
        ["journalctl", "-u", "ollama", "--since", f"-{minutes}min", "--no-pager", "-o", "short-unix"],
        capture_output=True, text=True, check=True,
    ).stdout
    times = []
    for line in out.splitlines():
        if "[GIN]" not in line:
            continue
        m = re.search(r'"(/[^"]*)"', line)
        if m and m.group(1) != "/" and not m.group(1).startswith(IGNORED_PATHS):
            times.append(float(line.split(" ", 1)[0]))
    return times


# vLLM のログで数えないリクエスト(死活の確認やモデルの一覧)
VLLM_IGNORED_PATHS = ("/health", "/metrics", "/version", "/v1/models", "/ping")


def vllm_request_times(minutes):
    """vLLM のコンテナのログにある、直近 minutes 分のリクエストの時刻(エポック秒)。
    ログは「2026-11-15T06:00:00.123456789Z INFO: 127.0.0.1:50000 - "POST /v1/chat/completions HTTP/1.1" 200 OK」の形(--timestamps)"""
    result = subprocess.run(["docker", "logs", "--since", f"{minutes}m", "--timestamps", "vllm"], capture_output=True, text=True, check=True)
    times = []
    for line in (result.stdout + result.stderr).splitlines():
        m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?Z .*"(?:GET|POST) (/[^ "]*) HTTP', line)
        if m and not m.group(2).startswith(VLLM_IGNORED_PATHS):
            times.append(float(calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"))))
    return times


def bootstrap_running():
    """user data(cloud-init)がまだ動いているか。S3 への保存は ready のあとも続くため、終わるまでは止めない"""
    result = subprocess.run(["cloud-init", "status"], capture_output=True, text=True)
    return "running" in result.stdout


def idle_check(minutes, dry_run=False):
    """Ollama へのリクエストが minutes 分なければインスタンスを止める。systemd のタイマーから数分ごとに呼ぶ。
    このまま使われなければ止まる時刻を idle_stop_at に書く(WebUI が止まる前に知らせるため)"""
    s = load_state()
    if s.get("state") not in ("ready", "failed") or bootstrap_running():
        log("起動処理の途中なので止めません")
        return

    window = minutes * 60
    with open(UPTIME_FILE, encoding="utf-8") as f:
        uptime = float(f.read().split()[0])
    # 起動処理が終わってから、また今回の起動(停止からの再開を含む)から minutes 分は止めない
    finished_at = parse_time(s.get("ready_at") or s["updated_at"])
    times = vllm_request_times(minutes) if engine() == "vllm" else ollama_request_times(minutes)
    stop_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max([time.time() - uptime, finished_at, *times]) + window))
    if stop_at != s.get("idle_stop_at"):
        # set_status は updated_at を変え、ready_at がないときの止める時刻の数え始めがずれるので、この項目だけ書く
        s["idle_stop_at"] = stop_at
        save_state(s)
    if uptime < window or time.time() - finished_at < window:
        return

    if times:
        log(f"直近 {minutes} 分に {len(times)} 件のリクエストがありました")
        return

    log(f"直近 {minutes} 分 Ollama へのリクエストがないため、インスタンスを止めます")
    set_status(s["stage"], s["state"], auto_stop={"reason": "idle", "idle_minutes": minutes, "at": now()})
    if not dry_run:
        # OS から止めると、EBS のインスタンスは停止になる(終了ではない)。スポットもリクエストは残り、あとで再開できる
        subprocess.run(["systemctl", "poweroff"], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("status", help="段階と状態を書く")
    p.add_argument("stage")
    p.add_argument("--state", choices=["running", "ready", "failed"], default="running")
    p.add_argument("--error")
    p.add_argument("--log")

    p = sub.add_parser("wait-server", help="推論エンジンが応答するまで待つ")
    p.add_argument("--timeout", type=int, default=300)

    p = sub.add_parser("track-download", help="Hugging Face からの取得の進み具合を書く(vLLM)")
    p.add_argument("directory")
    p.add_argument("expected_bytes", type=int)

    p = sub.add_parser("pull", help="モデルを取得する")
    p.add_argument("model")

    p = sub.add_parser("check-local", help="モデルが手元にあるか確かめる(閉じたネットワーク)")
    p.add_argument("model")

    p = sub.add_parser("verify", help="モデルが応答するか確かめる")
    p.add_argument("model")

    for command, help_text in [("restore-s3", "S3 からモデルをコピーする"), ("save-s3", "モデルを S3 に保存する")]:
        p = sub.add_parser(command, help=help_text)
        p.add_argument("model")
        p.add_argument("bucket")
        p.add_argument("models_dir")
        if command == "restore-s3":
            p.add_argument("--offline", action="store_true")

    p = sub.add_parser("mount-ebs", help="EBSボリュームを付ける")
    p.add_argument("volume_id")
    p.add_argument("mount_point")

    p = sub.add_parser("idle-check", help="リクエストがなければインスタンスを止める")
    p.add_argument("--minutes", type=int, required=True)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("bench", help="決まったプロンプトを流して TTFT と tok/s を測る")
    p.add_argument("model")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--num-predict", type=int, default=BENCH_NUM_PREDICT)

    args = parser.parse_args(argv)

    if args.command == "status":
        set_status(args.stage, args.state, error=args.error, log=args.log)
        return 0
    if args.command == "idle-check":
        # 起動処理とは別に動くので、失敗しても状態を failed にしない
        idle_check(args.minutes, args.dry_run)
        return 0
    if args.command == "track-download":
        # 進み具合を書くだけなので、失敗しても起動処理を止めない
        try:
            track_download(args.directory, args.expected_bytes)
        except Exception as e:  # noqa: BLE001
            log(f"取得の進み具合を書けませんでした: {e}")
        return 0
    if args.command == "bench":
        # 起動処理とは別に動くので、失敗しても状態を failed にしない
        bench(args.model, args.runs, args.num_predict)
        return 0

    try:
        if args.command == "wait-server":
            wait_server(args.timeout)
        elif args.command == "pull":
            pull(args.model)
        elif args.command == "check-local":
            check_local(args.model)
        elif args.command == "verify":
            verify(args.model)
        elif args.command == "restore-s3":
            restore_s3(args.model, args.bucket, args.models_dir, args.offline)
        elif args.command == "save-s3":
            save_s3(args.model, args.bucket, args.models_dir)
        elif args.command == "mount-ebs":
            mount_ebs(args.volume_id, args.mount_point)
    except Exception as e:
        # どの段階で何が起きたかを残す。ログの場所は bash のトラップが足す
        log(f"失敗しました: {e}")
        set_status(load_state().get("stage", "unknown"), "failed", error=f"{type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
