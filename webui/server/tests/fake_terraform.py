#!/usr/bin/env python3
"""テスト用の偽の terraform。受け取った引数と TF_WORKSPACE を出力し、少しずつ行を出す。

    validate は失敗(終了コード 1)、workspace list は default と qwen-test を返す
"""

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
sys.exit(1 if command == "validate" else 0)
