#!/usr/bin/env python3
"""Ollama のリリースファイルを、モデル用の S3 バケットに置く。閉じたネットワーク(network_mode = private)の準備に使う。

閉じたネットワークの GPUインスタンスは ollama.com に出られないため、バケットの
ollama/install/<バージョン>/ollama-linux-amd64.tar.zst から Ollama を入れる。インターネットのあるマシンで実行する。

    python3 scripts/upload_ollama.py                        # 最新版を置き、latest の下にも置く
    python3 scripts/upload_ollama.py --version 0.40.13      # バージョンを決めて置く(ollama_version = "0.40.13" で使う)
    python3 scripts/upload_ollama.py --region us-east-1 --profile my-profile

バケットは terraform/model-store で作ったもの(llm-launchpad-models-<アカウントID>-<リージョン>)を使う。
AWS CLI v2 と標準ライブラリだけを使う。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

RELEASE_API = "https://api.github.com/repos/ollama/ollama/releases/latest"
DOWNLOAD_URL = "https://github.com/ollama/ollama/releases/download/v{version}/ollama-linux-amd64.tar.zst"
# terraform/modules/bootstrap/main.tf の ollama_install_key と同じ形
INSTALL_KEY = "ollama/install/{version}/ollama-linux-amd64.tar.zst"
LATEST = "latest"


class AwsError(RuntimeError):
    pass


def aws(*args, region=None, profile=None):
    cmd = ["aws", *args]
    if region:
        cmd += ["--region", region]
    if profile:
        cmd += ["--profile", profile]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AwsError(f"aws {' '.join(args)} が失敗しました: {result.stderr.strip()}")
    return result.stdout


def latest_version():
    """GitHub の最新リリースのタグ(v0.40.13)から v を除いたもの"""
    with urllib.request.urlopen(RELEASE_API, timeout=30) as res:
        return json.load(res)["tag_name"].removeprefix("v")


def default_bucket(region, profile=None):
    account = aws("sts", "get-caller-identity", "--query", "Account", "--output", "text", region=region, profile=profile).strip()
    return f"llm-launchpad-models-{account}-{region}"


def install_keys(version, also_latest):
    keys = [INSTALL_KEY.format(version=version)]
    if also_latest:
        keys.append(INSTALL_KEY.format(version=LATEST))
    return keys


def download(version, dest):
    url = DOWNLOAD_URL.format(version=version)
    print(f"{url} を取得しています", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as res, open(dest, "wb") as f:
        while chunk := res.read(1 << 20):
            f.write(chunk)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", help="置く Ollama のバージョン(例: 0.40.13)。省略すると最新版を置き、latest の下にも置く")
    parser.add_argument("--region", default="ap-northeast-1", help="バケットのあるリージョン(既定 ap-northeast-1)")
    parser.add_argument("--bucket", help="置くバケット。省略すると terraform/model-store で作った名前を使う")
    parser.add_argument("--profile", help="AWS CLI のプロファイル名")
    args = parser.parse_args(argv)

    version = args.version or latest_version()
    try:
        bucket = args.bucket or default_bucket(args.region, args.profile)
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, "ollama-linux-amd64.tar.zst")
            download(version, local)
            # アップロードは1回だけにして、latest の分は S3 の中でコピーする
            source = None
            for key in install_keys(version, also_latest=args.version is None):
                target = f"s3://{bucket}/{key}"
                aws("s3", "cp", source or local, target, "--only-show-errors", region=args.region, profile=args.profile)
                source = source or target
                print(f"{target} に置きました")
    except AwsError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
