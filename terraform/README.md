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
│   ├── iam/                  # インスタンスロール(SSM、進み具合の書き込み、モデルバケットの読み書き)
│   ├── instance/             # GPUインスタンス(スポット / オンデマンド)と、モデル用EBSボリュームの付け外し
│   ├── bootstrap/            # user data(Ollama の導入とモデルの用意、アイドル停止のタイマー)と、進み具合を書く SSM パラメータ
│   └── lifetime/             # 最大稼働時間で止める EventBridge Scheduler のスケジュール
├── model-store/              # モデルの置き場所(S3バケット、EBSボリューム)。環境とは別の state で管理する
├── budget/                   # 月の予算のアラート(AWS Budgets)。環境とは別の state で管理する
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
* WebUI でも同じ計算をする(`webui/server/llm_launchpad_webui/sizing.py`)。結果が同じかを WebUI のテストで確かめているので、式を変えるときは両方を直す

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
* ルートボリュームは gp3 で暗号化する。既定の大きさは AMI(75 GiB)+ モデルの配布サイズの2倍 + 10 GiB。2倍にしているのは、取得中の一時ファイルや S3 からのコピーの分。EBS永続ではモデルをルートに置かないため、AMI + 10 GiB
* ルートボリュームのスループットは既定で 1000 MiB/s(IOPS 4000)にする。gp3 の既定の 125 MiB/s では、87GB のモデルのコピーとGPUへの読み込みに12分ほどかかるため。上げた分は、インスタンスを動かしている間だけ料金がかかる
* SSH の鍵は作らない。シェルもポートフォワードも SSM で行う
* IMDSv2 を必須にする

### 起動処理: user data で Ollama を入れ、進み具合を SSM Parameter Store に書く

初回起動時に user data(`modules/bootstrap/templates/user_data.sh.tftpl`)が次の順に進め、段階ごとの状態を SSM パラメータ `/llm-launchpad/<環境ID>/status` に JSON で書く。

| stage | 内容 |
|---|---|
| `instance_started` | user data が動き始めた |
| `installing_ollama` | Ollama を入れる |
| `preparing_storage` | モデルの置き場所を用意する(S3 からコピー、EBSボリュームを付ける)。そのあと Ollama を起動し、応答を待つ |
| `pulling_model` | Ollama library からモデルを取得する。`pull.percent` に何%かが入る。S3 や EBS にあったモデルは、manifest を確かめるだけで取り直さない |
| `verifying` | 短いプロンプトを1回送り、応答が返るかを確かめる。GPUへの読み込み時間と、nvidia-smi のメモリ使用量を記録する |
| `ready` | 使える状態(`state` も `ready`) |

* 状態は `terraform output -raw status_command | sh` で読める
* `stages` に各段階の開始時刻、`ready_at` に使える状態になった時刻が入る。コールドスタートの計測に使う
* 失敗すると `state` が `failed` になり、止まった `stage`、`error`(原因)、`log`(インスタンス上のログの場所)が入る
* 状態は手元の `/var/lib/llm-launchpad/status.json` にも書くので、パラメータに書けなくても起動処理は止まらない
* 取得中の進み具合は、10秒ごとか 5% 進むごとに書く(PutParameter の回数を抑えるため)
* インスタンスには、その環境のパラメータにだけ書ける権限(`ssm:PutParameter`、`ssm:GetParameter`)を付ける
* パラメータは Terraform が作るときの値だけを持ち、あとはインスタンスが書き換える(`ignore_changes`)。destroy で消える
* 補助スクリプト(`modules/bootstrap/files/launchpad.py`)は標準ライブラリと AWS CLI だけを使い、user data に埋め込む
* user data は 16KB までで、そのままでは約20KBになるため、gzip で圧縮して渡す(cloud-init が展開して実行する)。テストで圧縮後の大きさを確かめている

Ollama は見積もりと同じ条件で動かす(systemd の設定で渡す)。

| 環境変数 | 値 |
|---|---|
| `OLLAMA_MODELS` | モデルの置き場所。EBS永続は `/mnt/llm-models/ollama`、それ以外は `/usr/share/ollama/.ollama/models` |
| `OLLAMA_HOST` | ssm モードは `127.0.0.1:11434`、direct モードは `0.0.0.0:11434` |
| `OLLAMA_CONTEXT_LENGTH` | `context_length` |
| `OLLAMA_KV_CACHE_TYPE` | `kv_cache_type`。f16 以外では `OLLAMA_FLASH_ATTENTION=1` も付ける |
| `OLLAMA_NUM_PARALLEL` | `1`(同時リクエスト数。見積もりの前提) |
| `OLLAMA_KEEP_ALIVE` | `-1`(モデルをGPUに載せたままにする) |

* user data は初回起動時にしか動かないため、モデルなどを変えたらインスタンスを作り直す(`user_data_replace_on_change`)

### モデルの置き場所: 3つの方式を `model_storage` で切り替える

| model_storage | 起動時の動き | 停止中の固定費 | 使う前に |
|---|---|---|---|
| `s3_cache`(既定) | S3 からルートボリュームにコピーする。S3 になければ Ollama library から取得し、使える状態になったあとで S3 に保存する | S3 の保管料金(東京の Standard で約 $0.025/GB/月) | model-store でバケットを作る |
| `ebs` | モデル用の EBS ボリュームを付ける。初めて使うボリュームは初期化し、モデルがなければ取得する | EBS の保管料金(gp3 の東京で $0.096/GB/月 + 上げたスループットの分) | model-store でボリュームを作る |
| `download` | 毎回 Ollama library から取得する | なし | なし |

* S3 とEBSボリュームは、環境と一緒に destroy すると残せないため、別の構成 `model-store/` で管理する(リージョンごとに1回 apply する)
* S3 には Ollama の models ディレクトリと同じ構成(`manifests/` と `blobs/`)で、`ollama/models/` の下に置く。blob は内容のハッシュで名前が決まるので、同じ blob を共有するモデルは1回分しか置かない
* S3 への保存は blob を先に、manifest を最後に置く。manifest があれば、そのモデルは揃っているとみなす
* S3 からのコピーには AWS CLI の CRT 転送クライアントを使う
* S3 を直接マウントして読む方式(Mountpoint for Amazon S3 など)は、GPUへの読み込みが遅いため使わない
* EBS は作ったAZのインスタンスにしか付けられないため、EBS永続ではボリュームのあるAZに起動する(`availability_zone` を別のAZにすると plan で止める)
* EBSボリュームは fstab に `nofail` で書くので、スポットの中断で停止・再開しても付いたまま。destroy のときは、インスタンスを止めてから外す(マウントしたまま外すと中身が壊れることがあるため)
* バケットもボリュームも同じリージョンに置く。リージョンを変えると、そのリージョンの S3 やボリュームにモデルを置き直すことになる
* `status` の `cache` に、S3 にあったか(`hit`)、コピーや保存にかかった秒数、EBS を初期化したかが入る。コールドスタートを方式ごとに比べるときに使う

### 自動停止: 使われていなければ止め、長く動かしすぎたら止め、予算を超えそうなら知らせる

| 仕組み | 既定 | 動く場所 | 内容 |
|---|---|---|---|
| アイドル停止 | 30分(`idle_stop_minutes`) | インスタンスの中(systemd のタイマー) | 5分ごとに Ollama のログを見て、リクエストが N 分なければ OS から止める |
| 最大稼働時間 | 8時間(`max_lifetime_hours`) | インスタンスの外(EventBridge Scheduler) | インスタンスを起動してから X 時間たったら、StopInstances で止める |
| 予算アラート | 月 50 USD(`budget/`) | AWS Budgets | 実際の料金が予算の 50 / 80 / 100% を超えたときと、月末までの予測が予算を超えそうなときにメールで知らせる |

アイドル停止:

* リクエストは Ollama のログ(journald の `[GIN]` の行)から数える。`/api/version`、`/api/ps`、`/api/tags`、`/` は状態の確認なので数えない
* 起動処理の途中(状態が `running` か、cloud-init がまだ動いている間)は止めない。S3 への保存も、終わるまで待つ
* 起動処理が終わってから N 分、また停止から再開してから N 分は止めない
* 起動処理が失敗したまま放置されている場合も、N 分たてば止める
* 止めるときは状態の `auto_stop` に理由と時刻を書く。OS から止めるので、インスタンスは終了ではなく停止になる(スポットもリクエストは残り、あとで再開できる)
* SSM のポートフォワードがつながっていても、リクエストがなければ止める

最大稼働時間:

* インスタンスの中の仕組みが動いていなくても止まるよう、外から止める。EventBridge Scheduler の役割には、その環境のインスタンスへの `ec2:StopInstances` だけを付ける
* 数え始めはインスタンスを作ったとき。インスタンスを作り直すと数え直す。停止から再開しても数え直さない
* 1回だけ動くスケジュールなので、止まったあとに手で再開した場合は、もう止まらない(アイドル停止は動く)

予算アラート:

* 予算はアカウント単位なので、環境とは別の構成 `budget/` で管理する。既定ではアカウント全体の料金を数える
* `filter_by_project_tag = true` にすると `Project=llm-launchpad` のタグが付いた料金だけを数える。先に Billing のコンソールで `Project` をコスト配分タグとして有効にしておく(有効にする前の料金は数えられない)
* 通知はメールで直接届く(確認メールへの返信は要らない)

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
* ローカルの state は消えるとリソースだけが残るため、長く使うなら S3 backend に移す(`backend-s3.tf.example`)。消えた場合に備えて、`scripts/find_leftovers.py` でタグから探せるようにしてある

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
* 起動処理の進み具合を書くため、その環境の SSM パラメータにだけ `ssm:PutParameter`、`ssm:GetParameter` を付ける
* S3キャッシュのときだけ、モデルバケットの `ollama/` の下に `s3:GetObject`、`s3:PutObject`、`s3:AbortMultipartUpload` と、`ollama/` で絞った `s3:ListBucket` を付ける(ListBucket がないと、無いオブジェクトを確かめたときに 404 ではなく 403 になる)

## 使い方

最初に一度だけ、モデルの置き場所を作る(`model_storage = "download"` なら不要)。

```sh
cd terraform/model-store
cp terraform.tfvars.example terraform.tfvars   # EBS永続を使うなら model_volumes を書く
terraform init
terraform apply
```

`model-store` は環境を destroy しても残る。要らなくなったら `model-store` で destroy する(バケットにモデルが残っていると消せないので、先に空にする)。

予算のアラートも、最初に一度だけ作る。

```sh
cd terraform/budget
cp terraform.tfvars.example terraform.tfvars   # notification_emails と monthly_limit_usd を書く
terraform init
terraform apply
```

環境ごとの手順:

```sh
cd terraform
cp terraform.tfvars.example terraform.tfvars   # 必要なら値を書き換える
terraform init

# 環境ごとに workspace を作る(workspace 名が環境IDになる)
terraform workspace new qwen-test

terraform plan
terraform apply

# 起動処理の進み具合を見る(stage が ready になるまで待つ)
terraform output -raw status_command | sh

# 接続のしかたを表示する
terraform output ssm_session_command        # シェルで入る(失敗したときは /var/log/llm-launchpad-bootstrap.log を見る)
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
* S3キャッシュでバケットを作っていないと、plan が `reading S3 Bucket (llm-launchpad-models-...): couldn't find resource` で止まる。先に `model-store` を apply する

## 消し忘れの確認

`scripts/find_leftovers.py` が、全リージョンから llm-launchpad のリソースを探し、手元の state に載っていないものを一覧にする。読み取りだけで、リソースは変えない。

```sh
python3 scripts/find_leftovers.py                         # 全リージョン(1分ほどかかる)
python3 scripts/find_leftovers.py --regions ap-northeast-1
python3 scripts/find_leftovers.py --profile my-profile --json
```

| 探すもの | 見分け方 |
|---|---|
| EC2インスタンス、EBSボリューム、VPC、SSM パラメータ | `Project=llm-launchpad` タグ |
| スポットリクエスト | 見つけたインスタンスのリクエストか、インスタンスプロファイルの名前が `llm-lp-` で始まる(インスタンスがなく、リクエストだけ残ったものも見つける) |
| EventBridge Scheduler のスケジュール、IAM ロール | 名前が `llm-lp-` で始まる |
| S3 バケット | `Project=llm-launchpad` タグか、名前が `llm-launchpad-` で始まる |

* 各サービスの API で直接探す。Resource Groups Tagging API は、消したあともしばらく一覧に残ることがあるため使わない
* `terraform/` の下の state(環境の workspace、`model-store`、`budget`)にIDや名前があれば「state にある」とする。S3 backend の state は読まないので、`--state-dir` に `terraform state pull` で取り出した state を置いたディレクトリを渡す
* `Persistent=true` のタグ(`model-store` と `budget` が付ける)が付いたものは、state になくても消し忘れとは数えない
* 料金の目安は、インスタンスはスポット価格の履歴か Price List API の時間単価 x 今回の起動からの時間、EBS と S3 は月額で出す。単価はスクリプトに書かず、その都度問い合わせる
* 終了コードは、消し忘れなしで `0`、消し忘れは見つからなかったが調べられなかった種類やリージョンがあれば `1`、state にないリソースがあれば `2`(調べられなかったものがあっても `2` にする)
* 種類ごとに調べるので、1つの種類で失敗しても(権限がない、そのリージョンにサービスがないなど)、ほかの種類の結果は出す。失敗は `[エラー]` として出す
* 料金やバケットの大きさを取れなかっただけなら、`[注意]` を出して料金を `-` にする。リソースの一覧と終了コードには影響しない
* テストは `python3 -B -m unittest discover -s scripts/tests`(AWS に接続しない)

## テスト

AWS に接続せず、mock provider で plan の結果を確かめる(一部は mock のまま apply して出力を確かめる)。

```sh
terraform test
(cd model-store && terraform test)
(cd budget && terraform test)
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
| `model_storage` | `s3_cache` | モデルの置き場所(`s3_cache` / `ebs` / `download`) |
| `model_bucket_name` | `null`(model-store と同じ規則の名前) | S3キャッシュのバケット名 |
| `model_volume_name` | `default` | EBS永続で付けるボリュームの名前(model-store の `model_volumes` のキー) |
| `root_volume_size_gib` | `null`(自動) | ルートボリュームの大きさ(GiB) |
| `root_volume_throughput` | `1000` | ルートボリュームのスループット(MiB/s) |
| `root_volume_iops` | `4000` | ルートボリュームの IOPS(スループットの4倍以上) |
| `ollama_version` | `null`(最新) | 入れる Ollama のバージョン |
| `idle_stop_minutes` | `30` | Ollama へのリクエストがこの分数なければ止める(`0` で止めない) |
| `max_lifetime_hours` | `8` | 起動してからこの時間がたったら止める(`0` で止めない) |
| `region` | `ap-northeast-1` | リージョン |
| `aws_profile` | `null` | AWS CLI のプロファイル名 |
| `env_id` | `null`(workspace 名) | 環境ID。英小文字・数字・ハイフンの20文字以内 |
| `access_mode` | `ssm` | `ssm` / `direct` |
| `allowed_cidrs` | `[]` | `direct` のときに許可するIPv4 CIDR。空なら自分のIP |
| `vpc_cidr` | `10.42.0.0/16` | VPCのCIDR(/16〜/24) |
| `az_count` | `3` | サブネットを作るAZの数 |
| `extra_tags` | `{}` | 追加のタグ |
