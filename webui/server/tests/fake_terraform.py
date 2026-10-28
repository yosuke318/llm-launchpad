#!/usr/bin/env python3
"""テスト用の偽の terraform。受け取った引数と TF_WORKSPACE を出力し、少しずつ行を出す。

    validate は失敗(終了コード 1)、workspace list は default と qwen-test を返す
    output -json は、TF_WORKSPACE を入れた接続のしかたを返す
    state pull は、fake-state/<ワークスペース>.json を返す
    FAKE_TERRAFORM_FAIL にコマンド名(init など)を入れると、そのコマンドも失敗する
"""

import json
import os
import sys
import time

args = sys.argv[1:]
chdir = args.pop(0) if args and args[0].startswith("-chdir=") else ""
command = args[0] if args else ""

if command == "workspace":
    print("* default")
    print("  qwen-test")
    sys.exit(0)

if command == "output":
    ws = os.environ.get("TF_WORKSPACE", "")
    if os.environ.get("FAKE_TERRAFORM_FAIL") == "output":
        print("No outputs found", file=sys.stderr)
        sys.exit(1)
    values = {
        "instance_id": "i-0123456789abcdef0",
        "ollama_endpoint": "http://localhost:11434",
        "ssm_port_forward_command": f"aws ssm start-session --target i-0123456789abcdef0 # {ws}",
        "curl_example": "curl http://localhost:11434/api/generate",
        "vpc_id": "vpc-1",
    }
    print(json.dumps({k: {"value": v, "type": "string", "sensitive": False} for k, v in values.items()}))
    sys.exit(0)

if command == "state":
    # state pull は <terraform のディレクトリ>/fake-state/<ワークスペース>.json を返す。なければ空(まだ apply していない)
    if os.environ.get("FAKE_TERRAFORM_FAIL") == "state":
        print("Failed to load state", file=sys.stderr)
        sys.exit(1)
    path = os.path.join(chdir.removeprefix("-chdir="), "fake-state", f"{os.environ.get('TF_WORKSPACE', '')}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            print(f.read())
    sys.exit(0)

print(f"fake terraform {' '.join(args)}", flush=True)
print(f"workspace={os.environ.get('TF_WORKSPACE', '')} automation={os.environ.get('TF_IN_AUTOMATION', '')}", flush=True)
for arg in args:
    if arg.startswith("-var-file="):
        path = os.path.join(chdir.removeprefix("-chdir="), arg.removeprefix("-var-file="))
        with open(path, encoding="utf-8") as f:
            print(f"var-file: {f.read().strip()}", flush=True)
for i in range(3):
    time.sleep(0.05)
    print(f"line {i} 日本語", flush=True)
sys.exit(1 if command in ("validate", os.environ.get("FAKE_TERRAFORM_FAIL")) else 0)
