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

variable "model_bucket_name" {
  description = "モデルを置くS3バケット名。指定するとインスタンスロールに読み取り権限を付ける(バケット自体は YOSUKE-172 で作る)"
  type        = string
  default     = null

  validation {
    condition     = var.model_bucket_name == null || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.model_bucket_name))
    error_message = "model_bucket_name はS3バケット名の形式で指定してください。"
  }
}

variable "extra_tags" {
  description = "全リソースに追加で付けるタグ。Project / EnvId / ManagedBy は上書きできない"
  type        = map(string)
  default     = {}
}
