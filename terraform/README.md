# Terraform 構成

モデル指定でGPUインスタンスを立ち上げる構成の、土台(ネットワーク・セキュリティグループ・IAM)と、モデルに合うインスタンス型の自動選択。
GPUインスタンス本体や Ollama の導入は後から足す。

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
│   ├── sizing/               # VRAM見積もりとインスタンス型の選択(リソースは作らない)
│   ├── network/              # VPC、パブリックサブネット、IGW、S3 ゲートウェイエンドポイント
│   ├── security/             # インスタンス用セキュリティグループ
│   ├── iam/                  # インスタンスロール(SSM、モデルバケット読み取り)
│   └── instance/             # GPUインスタンス(スポット / オンデマンド)
└── tests/                    # terraform test(mock provider で AWS に接続せず確認)

catalog/                      # リポジトリ直下。WebUI からも読む
├── models.yaml               # モデル定義(会社、パラメータ数、量子化、層数など)
└── instance_types.yaml       # GPUインスタンス型の表
```

## 決めたこと

### インスタンス型: VRAMの見積もりから自動で選ぶ

`model`(Ollama のタグ)と `context_length` から必要VRAMを見積もり、収まる型を `catalog/instance_types.yaml` から選ぶ。

```
必要VRAM(GiB) = 重み + KVキャッシュ + 余裕
  重み         = 配布サイズ(GB) x 1e9 / 2^30
  KVキャッシュ = 2(K と V) x 注意層の数 x KVヘッド数 x ヘッド次元 x コンテキスト長 x 要素のバイト数(f16 は 2) / 2^30
  余裕         = vram_overhead_gib(既定 1.5)
```

* 型は合計VRAM(GPU枚数 x 1枚のVRAM)の小さい順に見て、最初に収まるものを選ぶ。同じVRAMなら表で上のもの(概ね安いもの)
* VRAMは nvidia-smi で見える値(T4 は 15 GiB など)で持つ。カタログ値の 16GB では判定しない
* 重みは「パラメータ数 x 0.6」ではなく配布サイズから出す。量子化の方式によっては一部の層を高い精度のまま残すため、パラメータ数からの計算は小さく出ることがある(nemotron-3-super は 72GB に対し実際は 87GB)
* MoE(gpt-oss など)も全エキスパートをVRAMに載せるため、配布サイズがそのまま重みになる
* Mamba や線形注意を混ぜたモデル(nemotron-3-super、qwen3-next、qwen3.5)は、KVキャッシュを持つ注意層の数(`attention_layers`)だけで計算する
* Ollama の同時リクエスト数(`OLLAMA_NUM_PARALLEL`)は 1 を前提にしている。増やすとKVキャッシュはその倍数になる
* `instance_type` で型を手で指定できる。収まらない場合は止めずに警告だけ出す(実測で確かめたい場合があるため)
* モデルがカタログにない、コンテキスト長がモデルの上限を超える、収まる型がない場合は plan で止める
* 料金は変わるためカタログに持たない(Price List API から取る)

例:

| model | context_length | 見積もり(GiB) | 選ばれる型 |
|---|---|---|---|
| `qwen3:8b` | 8192 | 4.84 + 1.13 + 1.5 = 7.47 | g4dn.xlarge(T4 15 GiB) |
| `qwen3:32b` | 8192 | 18.63 + 2.0 + 1.5 = 22.13 | g6.xlarge(L4 22.5 GiB) |
| `llama3.3:70b` | 8192 | 40.05 + 2.5 + 1.5 = 44.05 | g6e.xlarge(L40S 45 GiB) |
| `llama3.3:70b` | 32768 | 40.05 + 10 + 1.5 = 51.55 | g6.12xlarge(L4 x4 90 GiB) |
| `qwen3.5:122b` | 32768 | 75.44 + 0.75 + 1.5 = 77.69 | g6.12xlarge(L4 x4 90 GiB) |
| `nemotron-3-super:120b` | 32768 | 81.03 + 0.25 + 1.5 = 82.78 | g6.12xlarge(L4 x4 90 GiB) |

見積もりと実測の差は、GPUインスタンスで nvidia-smi の値と比べて確かめる。

### GPUインスタンス

* AMI は Deep Learning Base OSS Nvidia Driver GPU AMI(Ubuntu 24.04)の、リージョンごとの最新版を使う。NVIDIA ドライバ、CUDA、SSM エージェントが入っている
* 既定はスポット(`purchase_option = "spot"`)。中断されたら停止し、在庫が戻ると同じディスクのまま再開する(persistent のスポットリクエスト)
* destroy のときは、aws provider がスポットリクエストをキャンセルしてからインスタンスを終了する。リクエストだけが残って再起動されることはない
* AZ は、サブネットのあるAZのうちインスタンス型を提供しているAZから、名前の昇順で最初のものを選ぶ。東京では g5 / g6 / g6e は 1d で提供されていない
* ルートボリュームは gp3 で暗号化する。既定の大きさは AMI(75 GiB)+ モデルの配布サイズの2倍 + 10 GiB。2倍にしているのは、取得中の一時ファイルや S3 からのコピーの分
* SSH の鍵は作らない。シェルもポートフォワードも SSM で行う
* IMDSv2 を必須にする

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
* ローカルの state は消えるとリソースだけが残るため、長く使うなら S3 backend に移す(`backend-s3.tf.example`)。消えた場合に備えて、消し忘れ検出でタグから探せるようにしてある

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
* `model_bucket_name` を指定したときだけ、そのバケットの読み取り権限(`s3:ListBucket`、`s3:GetObject`)を付ける。バケット自体はモデルの置き場所の対応で作る

## 使い方

```sh
cd terraform
cp terraform.tfvars.example terraform.tfvars   # 必要なら値を書き換える
terraform init

# 環境ごとに workspace を作る(workspace 名が環境IDになる)
terraform workspace new qwen-test

terraform plan
terraform apply

# 接続のしかたを表示する
terraform output ssm_session_command        # シェルで入る
terraform output -raw ssm_port_forward_command | sh   # 別の端末で実行したままにする
terraform output -raw curl_example | sh
terraform output -raw openai_api_example | sh

# 使い終わったら必ず消す
terraform destroy
terraform workspace select default
terraform workspace delete qwen-test
```

* GPUインスタンスを起動するには、G系インスタンスの vCPU クォータ(オンデマンドとスポットは別枠)が必要
* SSM のポートフォワードには、手元に Session Manager プラグインが要る
* Ollama の導入とモデルの取得は、まだこの構成に入っていない。curl の例は、Ollama を入れたあとに使える

## テスト

AWS に接続せず、mock provider で plan の結果を確かめる(一部は mock のまま apply して出力を確かめる)。

```sh
terraform test
```

## 主な変数

| 変数 | 既定値 | 説明 |
|---|---|---|
| `model` | (必須) | 動かすモデル。Ollama のタグ(`catalog/models.yaml` のキー) |
| `context_length` | `8192` | コンテキスト長。KVキャッシュの見積もりに使う |
| `kv_cache_type` | `f16` | KVキャッシュの型(`f16` / `q8_0` / `q4_0`) |
| `vram_overhead_gib` | `1.5` | 重みとKVキャッシュのほかに見込む余裕(GiB) |
| `instance_type` | `null`(自動) | インスタンス型を手で指定する |
| `purchase_option` | `spot` | `spot` / `on-demand` |
| `availability_zone` | `null`(自動) | GPUインスタンスを起動するAZ |
| `root_volume_size_gib` | `null`(自動) | ルートボリュームの大きさ(GiB) |
| `region` | `ap-northeast-1` | リージョン |
| `aws_profile` | `null` | AWS CLI のプロファイル名 |
| `env_id` | `null`(workspace 名) | 環境ID。英小文字・数字・ハイフンの20文字以内 |
| `access_mode` | `ssm` | `ssm` / `direct` |
| `allowed_cidrs` | `[]` | `direct` のときに許可するIPv4 CIDR。空なら自分のIP |
| `vpc_cidr` | `10.42.0.0/16` | VPCのCIDR(/16〜/24) |
| `az_count` | `3` | サブネットを作るAZの数 |
| `model_bucket_name` | `null` | モデル置き場のS3バケット名 |
| `extra_tags` | `{}` | 追加のタグ |
