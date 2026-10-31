"""閉じたネットワークの VPC に置く Lambda。bedrock-runtime の VPC エンドポイント経由で Bedrock のモデルを呼ぶ。

VPC にはインターネットへの経路がなく、bedrock-runtime の名前はプライベートDNSでエンドポイントのIPになる。
呼ぶモデルは環境変数 MODEL_ID の1つだけ(IAM でもそのモデルだけ許可している)。

    {"action": "chat", "messages": [{"role": "user", "content": "こんにちは"}], "max_tokens": 512}
    {"action": "bench", "runs": 3}
    {"action": "endpoint"}   # bedrock-runtime の名前がどのIPになるか(VPC エンドポイント経由か確かめる)
"""

import datetime
import os
import socket
import time

import boto3

MODEL_ID = os.environ["MODEL_ID"]
REGION = os.environ["AWS_REGION"]
HOST = f"bedrock-runtime.{REGION}.amazonaws.com"

# terraform/modules/bootstrap/files/launchpad.py の BENCH_PROMPTS と同じ。Ollama の計測と比べられるようにする
BENCH_PROMPTS = [
    {"id": "short", "prompt": "Explain what a GPU is in three sentences."},
    {"id": "long", "prompt": "Write a detailed explanation of how HTTP caching works, covering Cache-Control, ETag, and Last-Modified."},
    {"id": "code", "prompt": "Write a Python function that returns the n-th Fibonacci number using memoization, with a short docstring."},
]
BENCH_MAX_TOKENS = 256
MAX_RUNS = 10

client = boto3.client("bedrock-runtime", region_name=REGION)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def endpoint():
    """bedrock-runtime の名前を引いた結果。VPC エンドポイント経由なら VPC の中のIPになる"""
    return {"host": HOST, "addresses": sorted({info[4][0] for info in socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)})}


def to_messages(messages):
    return [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages]


def chat(messages, max_tokens=512):
    started = time.monotonic()
    res = client.converse(modelId=MODEL_ID, messages=to_messages(messages), inferenceConfig={"maxTokens": int(max_tokens)})
    return {
        "model": MODEL_ID,
        "content": "".join(c.get("text", "") for c in res["output"]["message"]["content"]),
        "stop_reason": res.get("stopReason"),
        "usage": res.get("usage", {}),
        "total_sec": round(time.monotonic() - started, 3),
    }


def generate_timed(prompt, max_tokens):
    """converse_stream で送り、最初のトークンまでの時間(TTFT)と、出力の tok/s を測る。
    tok/s は、最初のトークンから最後までの時間で出力のトークン数を割る(Ollama の eval_duration に近い数え方)"""
    started = time.monotonic()
    res = client.converse_stream(
        modelId=MODEL_ID,
        messages=to_messages([{"role": "user", "content": prompt}]),
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
    )
    ttft = None
    usage = {}
    for event in res["stream"]:
        if ttft is None and event.get("contentBlockDelta", {}).get("delta", {}).get("text"):
            ttft = time.monotonic() - started
        if "metadata" in event:
            usage = event["metadata"].get("usage", {})
    total = time.monotonic() - started
    output_tokens = usage.get("outputTokens", 0)
    eval_sec = total - ttft if ttft is not None else 0
    return {
        "ttft_sec": None if ttft is None else round(ttft, 3),
        "total_sec": round(total, 3),
        "prompt_tokens": usage.get("inputTokens", 0),
        "output_tokens": output_tokens,
        "output_tokens_per_sec": round(output_tokens / eval_sec, 2) if eval_sec > 0 and output_tokens else None,
    }


def bench(runs=1, max_tokens=BENCH_MAX_TOKENS):
    runs = max(1, min(int(runs), MAX_RUNS))
    results = []
    for run in range(runs):
        for p in BENCH_PROMPTS:
            results.append({"prompt_id": p["id"], "run": run + 1, **generate_timed(p["prompt"], max_tokens)})

    def mean(key):
        values = [r[key] for r in results if r[key] is not None]
        return round(sum(values) / len(values), 3) if values else None

    return {
        "model": MODEL_ID,
        "measured_at": now(),
        "num_predict": max_tokens,
        "runs": runs,
        "ttft_sec": mean("ttft_sec"),
        "output_tokens_per_sec": mean("output_tokens_per_sec"),
        "prompt_tokens": sum(r["prompt_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
        "results": results,
        "endpoint": endpoint(),
    }


def handler(event, context):
    action = event.get("action", "chat")
    if action == "chat":
        return chat(event["messages"], event.get("max_tokens", 512))
    if action == "bench":
        return bench(event.get("runs", 1), event.get("max_tokens", BENCH_MAX_TOKENS))
    if action == "endpoint":
        return endpoint()
    raise ValueError(f"unknown action: {action}")
