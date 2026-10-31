"""Bedrock の環境(terraform/bedrock)。GPUインスタンスは起動せず、閉じたネットワークの VPC に置いた Lambda から、
bedrock-runtime の VPC エンドポイント経由でモデルを呼ぶ。

料金はトークン課金と、エンドポイント1つの時間課金。ベンチマークの $/100万トークンは、GPUインスタンスの
「1時間あたりの料金 ÷ 1時間に出せるトークン数」と比べられるよう、出力100万トークンを出すのにかかる料金
(計測で送った入力トークンの分も含む)にする。
"""

import os
import uuid

import yaml

# terraform/bedrock の model の既定と同じ
DEFAULT_MODEL = "jp.anthropic.claude-haiku-4-5-20251001-v1:0"

# terraform/bedrock で作るインターフェイスエンドポイントの数(bedrock-runtime だけ)
ENDPOINTS = 1

# 起動(/api/bedrock/launch)で tfvars に書ける変数と、その型
LAUNCH_VARIABLES = {
    "model": str,
    "region": str,
    "availability_zone": str,
}


def load_models(catalog_dir):
    """catalog/bedrock_models.yaml のモデル。id ごと"""
    with open(os.path.join(catalog_dir, "bedrock_models.yaml"), encoding="utf-8") as f:
        return {m["id"]: m for m in yaml.safe_load(f)["models"]}


def usd_per_million_output_tokens(prices, prompt_tokens, output_tokens):
    """出力100万トークンあたりの料金。入力の分は、計測と同じ割合で入力を送ったものとして足す"""
    if not prices or prices.get("input") is None or prices.get("output") is None or not output_tokens:
        return None
    cost = (prompt_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000
    return round(cost / output_tokens * 1_000_000, 4)


def summarize(raw, workspace, tfvars, prices):
    """Lambda で測った結果(raw)を、GPUインスタンスの計測(benchmark.summarize)と同じ形の1件にする。
    GPU と VRAM の項目はない"""
    tfvars = tfvars or {}
    return {
        "id": uuid.uuid4().hex[:12],
        "engine": "bedrock",
        "measured_at": raw.get("measured_at"),
        "workspace": workspace,
        "model": raw.get("model"),
        "region": tfvars.get("region"),
        "availability_zone": None,
        "instance_type": None,
        "gpu": None,
        "purchase_option": None,
        "model_storage": None,
        "context_length": None,
        "kv_cache_type": None,
        "runs": raw.get("runs"),
        "num_predict": raw.get("num_predict"),
        "ttft_sec": raw.get("ttft_sec"),
        "first_response_sec": raw.get("ttft_sec"),
        "thinking": False,
        "output_tokens_per_sec": raw.get("output_tokens_per_sec"),
        "prompt_tokens_per_sec": None,
        "warmup_load_sec": None,
        "vram_estimate_gib": None,
        "vram_measured_gib": None,
        "vram_diff_gib": None,
        "vram_total_gib": None,
        "hourly_usd": None,
        "input_usd_per_million": (prices or {}).get("input"),
        "output_usd_per_million": (prices or {}).get("output"),
        "usd_per_million_tokens": usd_per_million_output_tokens(prices, raw.get("prompt_tokens", 0), raw.get("output_tokens", 0)),
        "results": raw.get("results", []),
        "endpoint": raw.get("endpoint"),
        "loaded": None,
        "gpus": [],
    }
