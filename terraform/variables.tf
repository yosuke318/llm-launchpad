variable "region" {
  description = "環境を作るAWSリージョン"
  type        = string
  default     = "ap-northeast-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.region))
    error_message = "region は ap-northeast-1 のようなリージョン名で指定してください。"
  }
}

variable "aws_profile" {
  description = "使うAWS CLIのプロファイル名。null なら環境変数や既定の認証情報を使う"
  type        = string
  default     = null
}

variable "env_id" {
  description = "環境ID。リソース名と EnvId タグに使う。null なら terraform workspace 名を使う"
  type        = string
  default     = null
}

variable "access_mode" {
  description = "Ollama API への接続方法。ssm: インバウンドを開けずSSMポートフォワードで接続 / direct: allowed_cidrs から 11434 に直接接続"
  type        = string
  default     = "ssm"

  validation {
    condition     = contains(["ssm", "direct"], var.access_mode)
    error_message = "access_mode は ssm か direct を指定してください。"
  }
}

variable "network_mode" {
  description = "ネットワークの種類。public: パブリックサブネットからインターネットに出る / private: インターネットへの経路がないプライベートサブネットに起動し、SSM と S3 には VPC エンドポイントで接続する(Ollama とモデルは先に S3 か EBS に入れておく)"
  type        = string
  default     = "public"

  validation {
    condition     = contains(["public", "private"], var.network_mode)
    error_message = "network_mode は public か private を指定してください。"
  }

  validation {
    condition     = var.network_mode != "private" || var.access_mode == "ssm"
    error_message = "network_mode = private では access_mode = ssm だけを使えます(パブリックIPを付けないため)。"
  }

  validation {
    condition     = var.network_mode != "private" || var.model_storage != "download"
    error_message = "network_mode = private では model_storage = download は使えません(Ollama library に出られないため)。s3_cache か ebs を指定してください。"
  }
}

variable "allowed_cidrs" {
  description = "access_mode = direct のとき、Ollama API(11434)への接続を許可するIPv4 CIDR。空なら実行したマシンのグローバルIP(/32)を自動で使う"
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for c in var.allowed_cidrs : can(cidrnetmask(c))])
    error_message = "allowed_cidrs は 203.0.113.10/32 のようなIPv4 CIDRで指定してください。"
  }

  validation {
    condition     = alltrue([for c in var.allowed_cidrs : !can(cidrnetmask(c)) || cidrhost(c, 0) == split("/", c)[0]])
    error_message = "allowed_cidrs はネットワークアドレスで指定してください(例: 192.0.2.5/24 ではなく 192.0.2.0/24)。"
  }

  validation {
    condition     = alltrue([for c in var.allowed_cidrs : !endswith(c, "/0")])
    error_message = "allowed_cidrs に /0 (全開放)は指定できません。"
  }
}

variable "vpc_cidr" {
  description = "環境用VPCのCIDR。/16〜/24 の範囲で指定する(サブネットは /+8 で切る)"
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition = (
      can(cidrnetmask(var.vpc_cidr)) &&
      can(tonumber(split("/", var.vpc_cidr)[1])) &&
      try(tonumber(split("/", var.vpc_cidr)[1]) >= 16 && tonumber(split("/", var.vpc_cidr)[1]) <= 24, false)
    )
    error_message = "vpc_cidr は /16〜/24 のIPv4 CIDRで指定してください。"
  }
}

variable "az_count" {
  description = "サブネットを作るAZの数。スポットの在庫がAZごとに違うため、複数AZに用意しておく。リージョンのAZ数より多い場合はAZ数に合わせる"
  type        = number
  default     = 3

  validation {
    condition     = var.az_count >= 1 && var.az_count <= 6 && floor(var.az_count) == var.az_count
    error_message = "az_count は 1〜6 の整数で指定してください。"
  }
}

variable "model_storage" {
  description = "モデルの置き場所。s3_cache: S3 からコピーし、なければ取得して S3 に保存する / ebs: モデル用の EBS ボリュームを付ける / download: 毎回 Ollama library から取得する"
  type        = string
  default     = "s3_cache"

  validation {
    condition     = contains(["s3_cache", "ebs", "download"], var.model_storage)
    error_message = "model_storage は s3_cache / ebs / download のどれかを指定してください。"
  }
}

variable "model_volume_name" {
  description = "model_storage = ebs で付ける EBS ボリュームの名前(terraform/model-store の model_volumes のキー)"
  type        = string
  default     = "default"
}

variable "root_volume_throughput" {
  description = "ルートボリューム(gp3)のスループット(MiB/s)。S3 からのコピーとGPUへの読み込みが速くなる。125 を超える分は動かしている間だけ料金がかかる"
  type        = number
  default     = 1000

  validation {
    condition     = var.root_volume_throughput >= 125 && var.root_volume_throughput <= 1000
    error_message = "root_volume_throughput は 125〜1000 MiB/s の範囲で指定してください。"
  }
}

variable "root_volume_iops" {
  description = "ルートボリューム(gp3)の IOPS。スループット(MiB/s)は IOPS x 0.25 までしか出ないため、1000 MiB/s には 4000 IOPS が要る"
  type        = number
  default     = 4000

  validation {
    condition     = var.root_volume_iops >= 3000 && var.root_volume_iops <= 16000
    error_message = "root_volume_iops は 3000〜16000 の範囲で指定してください。"
  }

  validation {
    condition     = var.root_volume_throughput <= var.root_volume_iops * 0.25
    error_message = "root_volume_throughput は root_volume_iops x 0.25 以下にしてください(gp3 の制約)。"
  }
}

variable "model_bucket_name" {
  description = "model_storage = s3_cache でモデルを置く S3 バケット名。null なら llm-launchpad-models-<アカウントID>-<リージョン>(terraform/model-store で作る名前)"
  type        = string
  default     = null

  validation {
    condition     = var.model_bucket_name == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.model_bucket_name))
    error_message = "model_bucket_name はS3バケット名の形式で指定してください。"
  }
}

variable "model" {
  description = "動かすモデル。Ollama のタグで指定する(catalog/models.yaml のキー。例: qwen3:8b)"
  type        = string
}

variable "context_length" {
  description = "コンテキスト長(トークン)。KVキャッシュの見積もりに使う"
  type        = number
  default     = 8192

  validation {
    condition     = var.context_length >= 512 && floor(var.context_length) == var.context_length
    error_message = "context_length は 512 以上の整数で指定してください。"
  }
}

variable "kv_cache_type" {
  description = "KVキャッシュの型(Ollama の OLLAMA_KV_CACHE_TYPE)。q8_0 / q4_0 にすると必要VRAMが減る"
  type        = string
  default     = "f16"

  validation {
    condition     = contains(["f16", "q8_0", "q4_0"], var.kv_cache_type)
    error_message = "kv_cache_type は f16 / q8_0 / q4_0 のどれかを指定してください。"
  }
}

variable "vram_overhead_gib" {
  description = "重みとKVキャッシュのほかに見込む余裕(GiB)。CUDAコンテキストや計算用バッファの分"
  type        = number
  default     = 1.5

  validation {
    condition     = var.vram_overhead_gib >= 0 && var.vram_overhead_gib <= 8
    error_message = "vram_overhead_gib は 0〜8 の範囲で指定してください。"
  }
}

variable "instance_type" {
  description = "インスタンス型を手で指定する(catalog/instance_types.yaml にある型)。null なら見積もりから自動で選ぶ"
  type        = string
  default     = null
}

variable "purchase_option" {
  description = "GPUインスタンスの購入方法。spot: スポット(中断されると停止し、在庫が戻ると再開する) / on-demand: オンデマンド"
  type        = string
  default     = "spot"

  validation {
    condition     = contains(["spot", "on-demand"], var.purchase_option)
    error_message = "purchase_option は spot か on-demand を指定してください。"
  }
}

variable "availability_zone" {
  description = "GPUインスタンスを起動するAZ。null ならインスタンス型を提供しているAZから選ぶ"
  type        = string
  default     = null
}

variable "root_volume_size_gib" {
  description = "ルートボリュームの大きさ(GiB)。null なら AMI の大きさ + モデルの配布サイズの2倍 + 10 GiB"
  type        = number
  default     = null

  validation {
    condition     = var.root_volume_size_gib == null || try(var.root_volume_size_gib <= 16384 && floor(var.root_volume_size_gib) == var.root_volume_size_gib, false)
    error_message = "root_volume_size_gib は 16384 以下の整数で指定してください。"
  }
}

variable "ollama_version" {
  description = "GPUインスタンスに入れる Ollama のバージョン(例: 0.12.6)。null なら最新"
  type        = string
  default     = null

  validation {
    condition     = var.ollama_version == null || can(regex("^[0-9]+\\.[0-9]+\\.[0-9]+(-rc[0-9]+)?$", var.ollama_version))
    error_message = "ollama_version は 0.12.6 のような形式で指定してください。"
  }
}

variable "idle_stop_minutes" {
  description = "Ollama へのリクエストがこの分数なければ、GPUインスタンスを止める(5分ごとに確かめる)。0 なら止めない"
  type        = number
  default     = 30

  validation {
    condition     = var.idle_stop_minutes == 0 || (var.idle_stop_minutes >= 10 && var.idle_stop_minutes <= 1440 && floor(var.idle_stop_minutes) == var.idle_stop_minutes)
    error_message = "idle_stop_minutes は 0(止めない)か、10〜1440 の整数で指定してください。"
  }
}

variable "max_lifetime_hours" {
  description = "GPUインスタンスを起動してからこの時間がたったら止める(EventBridge Scheduler で外から止める)。0 なら止めない"
  type        = number
  default     = 8

  validation {
    condition     = var.max_lifetime_hours >= 0 && var.max_lifetime_hours <= 720 && floor(var.max_lifetime_hours) == var.max_lifetime_hours
    error_message = "max_lifetime_hours は 0(止めない)〜720 の整数で指定してください。"
  }
}

variable "extra_tags" {
  description = "全リソースに追加で付けるタグ。Project / EnvId / ManagedBy は上書きできない"
  type        = map(string)
  default     = {}
}
