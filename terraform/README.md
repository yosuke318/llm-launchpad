# Terraform 構成

モデル指定でGPUインスタンスを立ち上げる構成の、土台(ネットワーク・セキュリティグループ・IAM)。
GPUインスタンス本体や Ollama の導入は後続の Issue(YOSUKE-170 以降)で足す。

## ファイル構成

```
terraform/
├── versions.tf               # Terraform と provider のバージョン
├── providers.tf              # AWS provider(default_tags で管理用タグを付ける)
├── variables.tf              # 入力変数
├── locals.tf                 # 環境ID・タグ・許可CIDRの組み立て
├── main.tf                   # モジュールの呼び出し
├── outputs.tf                # 後続の構成やWebUIが使う出力
├── terraform.tfvars.example  # 変数のサンプル
├── backend-s3.tf.example     # state を S3 に置く場合のサンプル
├── modules/
│   ├── network/              # VPC、パブリックサブネット、IGW、S3 ゲートウェイエンドポイント
│   ├── security/             # インスタンス用セキュリティグループ
│   └── iam/                  # インスタンスロール(SSM、モデルバケット読み取り)
└── tests/                    # terraform test(mock provider で AWS に接続せず確認)
```

## 決めたこと

### VPC: 環境ごとに専用VPCを作る

* 既定VPCは削除されているアカウントもあり、前提にできないため使わない
* 環境ごとに分けると、destroy で全部きれいに消える
* NATゲートウェイは時間課金があるため使わない。パブリックサブネット + パブリックIPで外に出て、インバウンドはセキュリティグループで閉じる
* スポットの在庫はAZごとに違うため、既定で3つのAZにサブネットを用意する(`az_count`)
* S3 のゲートウェイエンドポイント(無料)を付けて、モデル取得の通信をVPC内で完結させる
* VPCはリージョンあたり既定で5個までなので、同時に立てる環境は5つまで(GPUのvCPUクォータのほうが先に効くことが多い)

### 接続方法: 既定は SSM

| access_mode | インバウンド | 接続のしかた |
|---|---|---|
| `ssm`(既定) | 開けない | SSM のポートフォワードで `localhost:11434` に転送する |
| `direct` | `allowed_cidrs` から 11434/tcp だけ | `http://<パブリックIP>:11434` に直接接続する |

* `direct` で `allowed_cidrs` を空にすると、実行したマシンのグローバルIP(/32)を自動で使う
* `0.0.0.0/0` などの全開放は指定できない
* アウトバウンドは HTTPS(443)と HTTP(80)だけ許可する(SSM、S3、Ollama library、Hugging Face、OSのパッケージミラー)

### state: 既定はローカル、1 workspace = 1 環境

* すぐ試せるよう、既定はローカルの state(`terraform.tfstate.d/<workspace名>/`)にする
* 環境ごとに workspace を分ける。workspace 名がそのまま環境ID(`EnvId` タグ、リソース名)になる
* ローカルの state は消えるとリソースだけが残るため、長く使うなら S3 backend に移す(`backend-s3.tf.example`)。消えた場合に備えて、消し忘れ検出(YOSUKE-174)でタグから探せるようにしてある

### タグ

全リソースに次のタグを付ける(`aws_route` などタグに対応していないリソースを除く)。

| タグ | 値 |
|---|---|
| `Project` | `llm-launchpad` |
| `EnvId` | 環境ID(workspace 名) |
| `ManagedBy` | `terraform` |
| `CreatedAt` | 初回 apply の時刻(RFC 3339) |

`Project` / `EnvId` / `ManagedBy` は provider の `default_tags` で付ける。`CreatedAt` は plan のたびに変わらないよう `time_static` から取り、各リソースの `tags` で付ける。

### IAM

* `AmazonSSMManagedInstanceCore`: SSM でのシェル接続とポートフォワードに使う
* `model_bucket_name` を指定したときだけ、そのバケットの読み取り権限(`s3:ListBucket`、`s3:GetObject`)を付ける。バケット自体は YOSUKE-172 で作る

## 使い方

```sh
cd terraform
cp terraform.tfvars.example terraform.tfvars   # 必要なら値を書き換える
terraform init

# 環境ごとに workspace を作る(workspace 名が環境IDになる)
terraform workspace new qwen-test

terraform plan
terraform apply

# 使い終わったら必ず消す
terraform destroy
terraform workspace select default
terraform workspace delete qwen-test
```

## テスト

AWS に接続せず、mock provider で plan の結果を確かめる。

```sh
terraform test
```

## 主な変数

| 変数 | 既定値 | 説明 |
|---|---|---|
| `region` | `ap-northeast-1` | リージョン |
| `aws_profile` | `null` | AWS CLI のプロファイル名 |
| `env_id` | `null`(workspace 名) | 環境ID。英小文字・数字・ハイフンの20文字以内 |
| `access_mode` | `ssm` | `ssm` / `direct` |
| `allowed_cidrs` | `[]` | `direct` のときに許可するIPv4 CIDR。空なら自分のIP |
| `vpc_cidr` | `10.42.0.0/16` | VPCのCIDR(/16〜/24) |
| `az_count` | `3` | サブネットを作るAZの数 |
| `model_bucket_name` | `null` | モデル置き場のS3バケット名 |
| `extra_tags` | `{}` | 追加のタグ |
