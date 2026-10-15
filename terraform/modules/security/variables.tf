variable "name" {
  description = "Name タグとセキュリティグループ名の接頭辞"
  type        = string
}

variable "vpc_id" {
  description = "セキュリティグループを作るVPCのID"
  type        = string
}

variable "ollama_port" {
  description = "Ollama API のポート"
  type        = number
  default     = 11434
}

variable "allowed_cidrs" {
  description = "Ollama API への接続を許可するIPv4 CIDR。空ならインバウンドを開けない"
  type        = list(string)
  default     = []
}

variable "tags" {
  description = "各リソースに追加で付けるタグ"
  type        = map(string)
  default     = {}
}
