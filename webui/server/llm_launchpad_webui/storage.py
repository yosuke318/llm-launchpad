"""モデルの置き場所(ストレージ方式)ごとの、料金と起動時間の目安。

起動時間は、実機で測った値から割り出す(2026-10-24、東京、g4dn.xlarge のスポット、qwen3:8b = 5.2GB)。
モデルが大きいほど、取得やコピー、GPU への読み込みが長くなる。インスタンス型やリージョン、混み具合で変わるため目安。

    起動時間 = インスタンスの起動 + Ollama の導入 + モデルの用意 + GPU への読み込みと最初の応答
"""

import math

# terraform/variables.tf と terraform/model-store/variables.tf の既定値
ROOT_THROUGHPUT_MIBPS = 1000
ROOT_IOPS = 4000
MODEL_VOLUME_THROUGHPUT_MIBPS = 250
MODEL_VOLUME_IOPS = 3000
# gp3 は 3000 IOPS、125 MiB/s までは容量の料金に含まれる
GP3_FREE_IOPS = 3000
GP3_FREE_THROUGHPUT_MIBPS = 125
# Deep Learning Base AMI(Ubuntu 24.04)のルートボリューム
AMI_ROOT_GIB = 75
HOURS_PER_MONTH = 730
MIB = 1024 * 1024

# 実機で測った値(qwen3:8b、5.2GB)
MEASURED = {
    "source": "2026-10-24 ap-northeast-1 g4dn.xlarge spot qwen3:8b (5.2GB)",
    # apply してから user data が動き始めるまで(スポットの起動とインスタンスの起動)。6〜8分だった
    "boot_sec": 420,
    "install_ollama_sec": 15,
    # Ollama library からの取得: 5.2GB を約38秒
    "download_bytes_per_sec": 137e6,
    # S3 からのコピー(CRT 転送クライアント): 5.2GB を16.2秒
    "s3_copy_bytes_per_sec": 320e6,
    # S3 や EBS にあったモデルを、Ollama が manifest だけ確かめる時間
    "pull_check_sec": 4,
    # GPU への読み込みと最初の応答: ルートボリューム(1000 MiB/s)で約90秒、EBS(250 MiB/s)で約111秒。
    # ディスクの速さで決まらない分(CUDA の初期化など)を、ここに置く
    "load_fixed_sec": 85,
}

# モデルの置き場所 s3_files(Amazon S3 Files)の目安。実機ではまだ測っていない。
# 読み込みの速さは、ほかの人の検証(1GB を単純に読んで約 640 MB/s)を使う。公称は1台あたり最大 3 GiB/s
S3_FILES = {
    "read_bytes_per_sec": 640e6,
    # amazon-efs-utils を入れてマウントするまで
    "mount_sec": 60,
}

# vLLM(推論エンジン)の目安。実機ではまだ測っていない(Ollama の測定と、イメージの大きさからの見込み)
VLLM = {
    # イメージ(vllm/vllm-openai)の取得。CUDA を含み 10GB ほど
    "image_pull_sec": 240,
    # GPU への読み込みのほかにかかる分(CUDA グラフの用意、torch.compile など)
    "load_fixed_sec": 180,
    # イメージの分、ルートボリュームに足す(terraform/locals.tf の vllm_root_volume_gib)
    "root_volume_extra_gib": 30,
}


def _round(v, digits=4):
    return None if v is None else round(v, digits)


def gp3_monthly(prices, size_gib, throughput_mibps, iops):
    """gp3 ボリュームの月額。容量 + 上げたスループット + 上げた IOPS"""
    if not prices or None in (prices.get("storage_per_gb_month"), prices.get("throughput_per_mibps_month"), prices.get("iops_per_iops_month")):
        return None
    return (
        size_gib * prices["storage_per_gb_month"]
        + max(throughput_mibps - GP3_FREE_THROUGHPUT_MIBPS, 0) * prices["throughput_per_mibps_month"]
        + max(iops - GP3_FREE_IOPS, 0) * prices["iops_per_iops_month"]
    )


def model_volume_size_gib(file_size_gb):
    """model-store で作る EBS ボリュームの大きさの目安。モデル + 2割 + 10 GiB"""
    return math.ceil(file_size_gb * 1.2) + 10


def root_volume_size_gib(file_size_gb, models_on_root, extra_gib=0):
    """terraform/modules/instance のルートボリュームの既定の大きさ。extra_gib は vLLM のイメージなどの分"""
    return AMI_ROOT_GIB + (math.ceil(file_size_gb * 2) if models_on_root else 0) + 10 + extra_gib


def startup_stage_sec(file_size_gb, storage, cached, engine="ollama"):
    """起動処理の段階ごとの目安(秒)。段階は起動の進み具合の画面と同じ。cached は S3 や EBS にモデルが置いてある場合。
    booting は apply を始めてから user data が動き始めるまで(インスタンスの作成と起動)。
    vLLM はモデルを毎回 Hugging Face から取得する(Ollama library と同じ速さと見込む)"""
    size = file_size_gb * 1e9
    m = MEASURED
    if engine == "vllm":
        return {
            "booting": m["boot_sec"],
            "installing_ollama": VLLM["image_pull_sec"],
            "preparing_storage": 0,
            "pulling_model": size / m["download_bytes_per_sec"],
            "verifying": VLLM["load_fixed_sec"] + size / (ROOT_THROUGHPUT_MIBPS * MIB),
        }
    download = size / m["download_bytes_per_sec"]
    throughput = MODEL_VOLUME_THROUGHPUT_MIBPS if storage == "ebs" else ROOT_THROUGHPUT_MIBPS
    if storage == "s3_files":
        # コピーはしない。モデルがバケットになければ取得してマウントに書き(S3 Files がバケットへ書き戻す)、
        # GPU への読み込みはマウント(S3)から読む
        return {
            "booting": m["boot_sec"],
            "installing_ollama": m["install_ollama_sec"],
            "preparing_storage": S3_FILES["mount_sec"],
            "pulling_model": m["pull_check_sec"] if cached else download,
            "verifying": m["load_fixed_sec"] + size / S3_FILES["read_bytes_per_sec"],
        }
    if storage == "s3_cache" and cached:
        prepare, pull = size / m["s3_copy_bytes_per_sec"], m["pull_check_sec"]
    elif storage == "ebs" and cached:
        prepare, pull = 0, m["pull_check_sec"]
    else:
        prepare, pull = 0, download
    return {
        "booting": m["boot_sec"],
        "installing_ollama": m["install_ollama_sec"],
        "preparing_storage": prepare,
        "pulling_model": pull,
        "verifying": m["load_fixed_sec"] + size / (throughput * MIB),
    }


def startup_sec(file_size_gb, storage, cached, engine="ollama"):
    """起動処理の目安(秒)。cached は S3 や EBS にモデルが置いてある場合"""
    return round(sum(startup_stage_sec(file_size_gb, storage, cached, engine).values()))


def storage_options(file_size_gb, gp3_prices, s3_per_gb_month, model_store, engine="ollama"):
    """3つの方式の比較。保管の月額(止めている間もかかる分)、動かしている間の1時間あたりのディスクの料金、起動時間の目安。
    vLLM では毎回ダウンロードだけ(file_size_gb は bf16 の重みの大きさ)"""
    if engine == "vllm":
        extra = VLLM["root_volume_extra_gib"]
        monthly = gp3_monthly(gp3_prices, root_volume_size_gib(file_size_gb, True, extra), ROOT_THROUGHPUT_MIBPS, ROOT_IOPS)
        return [{
            "id": "download",
            "storage_monthly_usd": 0.0,
            "root_volume_gib": root_volume_size_gib(file_size_gb, True, extra),
            "running_disk_hourly_usd": _round(None if monthly is None else monthly / HOURS_PER_MONTH),
            "startup_first_sec": startup_sec(file_size_gb, "download", False, engine),
            "startup_cached_sec": None,
            "ready": True,
            "prerequisite": None,
        }]
    store = model_store or {}
    volume_gib = model_volume_size_gib(file_size_gb)

    def root_hourly(models_on_root):
        monthly = gp3_monthly(gp3_prices, root_volume_size_gib(file_size_gb, models_on_root), ROOT_THROUGHPUT_MIBPS, ROOT_IOPS)
        return None if monthly is None else monthly / HOURS_PER_MONTH

    s3_monthly = None if s3_per_gb_month is None else file_size_gb * s3_per_gb_month
    ebs_monthly = gp3_monthly(gp3_prices, volume_gib, MODEL_VOLUME_THROUGHPUT_MIBPS, MODEL_VOLUME_IOPS)
    # EBS は容量よりスループットの料金が大きいことがあるため、内訳も出す
    ebs_throughput_monthly = None if not gp3_prices or gp3_prices.get("throughput_per_mibps_month") is None else (
        max(MODEL_VOLUME_THROUGHPUT_MIBPS - GP3_FREE_THROUGHPUT_MIBPS, 0) * gp3_prices["throughput_per_mibps_month"]
    )

    return [
        {
            "id": "s3_cache",
            "storage_monthly_usd": _round(s3_monthly),
            "root_volume_gib": root_volume_size_gib(file_size_gb, True),
            "running_disk_hourly_usd": _round(root_hourly(True)),
            "startup_first_sec": startup_sec(file_size_gb, "s3_cache", cached=False),
            "startup_cached_sec": startup_sec(file_size_gb, "s3_cache", cached=True),
            "ready": bool(store.get("bucket_exists")),
            "prerequisite": None if store.get("bucket_exists") else "bucket",
        },
        {
            "id": "ebs",
            "storage_monthly_usd": _round(ebs_monthly),
            "storage_throughput_monthly_usd": _round(ebs_throughput_monthly),
            "volume_gib": volume_gib,
            "volume_throughput_mibps": MODEL_VOLUME_THROUGHPUT_MIBPS,
            "root_volume_gib": root_volume_size_gib(file_size_gb, False),
            "running_disk_hourly_usd": _round(root_hourly(False)),
            "startup_first_sec": startup_sec(file_size_gb, "ebs", cached=False),
            "startup_cached_sec": startup_sec(file_size_gb, "ebs", cached=True),
            "ready": bool(store.get("volumes")),
            "prerequisite": None if store.get("volumes") else "volume",
        },
        {
            # 試験的。保管は s3_cache と同じバケット(S3 Files の速いストレージには小さいファイルだけが載る)
            "id": "s3_files",
            "storage_monthly_usd": _round(s3_monthly),
            "root_volume_gib": root_volume_size_gib(file_size_gb, False),
            "running_disk_hourly_usd": _round(root_hourly(False)),
            "startup_first_sec": startup_sec(file_size_gb, "s3_files", cached=False),
            "startup_cached_sec": startup_sec(file_size_gb, "s3_files", cached=True),
            "ready": bool(store.get("bucket_exists")) and store.get("bucket_versioning") is True,
            "prerequisite": None if (store.get("bucket_exists") and store.get("bucket_versioning") is True)
            else ("bucket" if not store.get("bucket_exists") else "versioning"),
            "experimental": True,
        },
        {
            "id": "download",
            "storage_monthly_usd": 0.0,
            "root_volume_gib": root_volume_size_gib(file_size_gb, True),
            "running_disk_hourly_usd": _round(root_hourly(True)),
            "startup_first_sec": startup_sec(file_size_gb, "download", cached=False),
            "startup_cached_sec": None,
            "ready": True,
            "prerequisite": None,
        },
    ]
