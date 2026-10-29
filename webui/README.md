# WebUI

手元で動かす画面。画面で選んだ値を tfvars に書き、`terraform/` で terraform を子プロセスとして実行して、出力を画面に流す。

```
webui/
├── server/   # FastAPI。terraform の実行と、出力の Server-Sent Events(SSE)。Python は uv で管理する
└── web/      # React + Vite + TypeScript。日本語と英語の文言は src/locales/{ja,en}.json
```

## 起動

```sh
# 画面をビルドする(webui/web/dist ができる)
cd webui/web
npm install
npm run build

# サーバーを起動する。ビルドした画面も同じサーバーが配る
cd ../server
uv run uvicorn llm_launchpad_webui.app:app --host 127.0.0.1 --port 8000
```

ブラウザで http://127.0.0.1:8000 を開く。

* 料金がかかる操作ができるため、`127.0.0.1` だけで待ち受ける。`--host 0.0.0.0` にしない
* AWS の認証情報は、サーバーを起動した端末のもの(環境変数や `~/.aws`)をそのまま使う

### 画面を直しながら動かす

```sh
cd webui/server && uv run uvicorn llm_launchpad_webui.app:app --host 127.0.0.1 --port 8000 --reload
cd webui/web && npm run dev    # http://127.0.0.1:5173 。/api はサーバー(8000)に中継する
```

`--reload` はサーバーのコードを直すと再起動するため、そのとき実行中の terraform も止まる。plan 以外を動かすときは `--reload` を付けない。

## 決めたこと

### 技術スタック

* サーバーは FastAPI。起動処理(`terraform/modules/bootstrap/files/launchpad.py`)や消し忘れ検出(`scripts/find_leftovers.py`)と同じ Python にして、AWS への問い合わせやベンチマークを同じ言語で書けるようにする
* 画面は React。チャットのストリーミング表示や、料金の確認、進み具合の画面を作るため
* 日本語と英語は react-i18next で切り替える。文言は `web/src/locales/ja.json` と `en.json` に分け、キーがそろっているかをテストで確かめる。選んだ言語はブラウザに覚えておく
* サーバーが返すエラーや警告の文言も、日本語と英語を `server/llm_launchpad_webui/messages.py` に置く。画面は表示している言語を `Accept-Language` で送り、サーバーはそれが英語(`en`)で始まれば英語、それ以外は日本語で返す。AWS や terraform が返す詳しいメッセージは訳さずに後ろに付ける。両方の言語に同じ値が入るかはテストで確かめる
* terraform の output(curl の例のプロンプト)と、インスタンスの起動処理が書く失敗の理由は、日本語のまま

### UI は tfvars を作るだけの薄い層にする

* 画面から渡した変数は `terraform/tfvars/<ワークスペース>.tfvars.json` に書き、`-var-file` で渡す。同じファイルを使えば、terraform を手で実行しても同じ結果になる

  ```sh
  cd terraform
  TF_WORKSPACE=qwen-test terraform plan -var-file=tfvars/qwen-test.tfvars.json
  ```

* `terraform/tfvars/` は自分のIPなどが入ることがあるため、git に入れない
* ワークスペースは `TF_WORKSPACE` で選ぶ。`terraform workspace select` はしないので、手元で選んでいるワークスペースは変わらない

### モデルを選ぶ画面

* 提供元(会社)で絞り、モデルの一覧に、パラメータ数、必要ストレージ(配布サイズ)、必要VRAM、推奨インスタンス型、ライセンスを並べる
* コンテキスト長と KV キャッシュの型を変えると、必要VRAMと推奨のインスタンス型が変わる。モデルの最大コンテキスト長を超えたら、最大で見積もる
* モデルを選ぶと、VRAM の内訳(重み / KVキャッシュ / 余裕 / 残り)と、収まるインスタンス型を出す。残りが 1 GiB を切ったら注意を出す
* データは `catalog/models.yaml` と `catalog/instance_types.yaml` を読む。モデル定義を足したら、サーバーを起動し直す
* 推論エンジンは Ollama だけ(vLLM は選択欄だけ置いて、選べないようにしてある)

### リージョンとモデルの置き場所を選ぶ

モデルを選ぶと、その下に出る。

* リージョンを選ぶ(既定は東京)。推奨のインスタンス型を使える AZ、オンデマンドの単価、スポットの単価(使える AZ の中で一番安いもの)を出す
* モデルの置き場所(S3 キャッシュ / EBS 永続 / 毎回ダウンロード)を選ぶ。方式ごとに、保管の料金(止めている間もかかる月額)と起動時間の目安を並べる
* EBS 永続は、容量よりスループット(250 MiB/s)の料金のほうが大きいことが多いので、その内訳も出す
* 選んだ方式で、1時間あたりの目安 = インスタンス + ルートボリューム + 保管の料金(月額 ÷ 730)を出す
* そのリージョンに model-store のバケットやボリュームがなければ、先に `terraform/model-store` を apply するよう案内する。既定と違うリージョンを選んだら、モデルを置き直すことになると案内する
* 単価は AWS の Price List API とスポット価格の履歴から、選んだリージョンの値を取る(`server/llm_launchpad_webui/aws_info.py`)。同じ問い合わせは1時間(スポット価格は10分、model-store は1分)覚えておく
* 料金を1つ取れなくても、ほかは出す。取れなかったものは「不明」にして、理由を注意に出す
* 起動時間は、実機の測定(2026-10-24、東京、g4dn.xlarge のスポット、qwen3:8b = 5.2GB)から割り出す(`server/llm_launchpad_webui/storage.py`)。インスタンスの起動 約7分 + Ollama の導入 + モデルの用意(取得 137 MB/s、S3 からのコピー 320 MB/s)+ GPU への読み込み(85秒 + ディスクから読む時間)
* AWS の認証情報は、サーバーを起動した端末のものを使う。読み取りだけで、AWS のリソースは作らない

### 起動前のチェック

リージョンとモデルの置き場所の下に出る。1つでも問題(✕)があれば、その購入方法では起動の確認に進めない。

| チェック | 内容 | 問題があったときの対処として出すもの |
|---|---|---|
| AWS の認証情報 | `sts get-caller-identity` が通るか。アカウント、プロファイル、ARN を出す | `aws configure` か `AWS_PROFILE` を設定して、サーバーを起動し直す |
| インスタンス型を使えるか | そのリージョンでインスタンス型を提供している AZ があるか | 別のリージョンか、別のインスタンス型にする |
| vCPU クォータ(オンデマンド / スポット) | G 系と VT 系のクォータの上限 - 動いている G 系と VT 系の vCPU >= 必要な vCPU か。オンデマンドとスポットは別枠 | 引き上げの申請ページへのリンク。申請中のものがあれば、その値と状態 |

* 認証情報が通らなければ、ほかのチェックはしない
* 権限がないなどで確かめられなかったもの(?)は、起動を止めない。apply で失敗することがあると案内する
* 片方の購入方法だけ通るときは、そちらなら起動できると案内する
* クォータと使用中の vCPU は1分(使用中は30秒)覚えておく。申請が通ったら「もう一度確かめる」を押す

### 起動の確認

起動前のチェックの下の「この内容で起動を確認する」を押すと、確認のポップアップが出る。チェックでスポットもオンデマンドも通らなければ、押せない。

* 1時間あたりの目安(選んだ購入方法での合計)と、止めている間の保管の料金を一番上に出す
* モデル、ライセンス、インスタンス型、リージョン、モデルの置き場所と起動時間の目安、ディスク容量、コンテキスト長と量子化、必要VRAMの内訳を並べる
* 購入方法(スポット / オンデマンド)を選ぶ。チェックが通らなかった方は選べない。スポットでは、AWS の Spot placement score(1〜10)から中断されやすさを出す(7以上は低い、4以上は中くらい、それより下は高い)。AWS の見立てで、実際とは違うことがある(東京で 1/10 でも起動できた)ので、止めはしない
* 自動停止(アイドルで止めるまでの分、最大の稼働時間)と環境ID(既定はモデル名から作る)を変えられる。書き込む tfvars の中身もそのまま見られる
* 「はい」で、表示した内容を `terraform/tfvars/<環境ID>.tfvars.json` に書き、`terraform init` と `terraform apply -auto-approve` を続けて実行する。起動の進み具合のタブに切り替わる。出力は Terraform のタブで見られる。init が失敗したら apply はしない
* 「いいえ」なら閉じるだけで、何も書かない

### 起動の進み具合

起動の確認で「はい」を押すと、このタブに切り替わる。画面から起動した環境(tfvars を書いたもの)を選んで見ることもできる(既定は最後に起動した環境)。

| 段階 | どこから読むか |
|---|---|
| インスタンスの作成 | terraform apply の実行(サーバーのメモリにある最後の起動) |
| インスタンスの起動 | apply が終わってから、インスタンスが Ollama を入れ始めるまで |
| Ollama の導入 / モデルの置き場所の用意 / モデルの取得 / 応答の確認 / 使える状態 | インスタンスが SSM パラメータ `/llm-launchpad/<環境ID>/status` に書く `stage`(`terraform/README.md` の起動処理の表) |

* 使える状態か失敗するまで、5秒ごとに読み直す。モデルの取得中は、何%か(`pull.percent`)と、取得した大きさを出す
* 経過時間は apply を始めたときから数え、使える状態になったか失敗したときで止める
* 残り時間の目安は、段階ごとの目安(`server/llm_launchpad_webui/storage.py` の `startup_stage_sec`。起動時間の目安と同じ実機の測定から出す)から、今の段階で過ぎた時間を引いて足し合わせる。モデルの取得中は、取得の速さから出す。S3 や EBS にモデルがあるか分かるまでは、取得し直す前提で長めに見積もる
* apply の途中は、SSM パラメータを読まない(前に起動したときの状態が残っていることがあるため)
* 失敗したときは、止まった段階を出す。apply で止まったら terraform の出力の最後の40行、インスタンスで止まったら原因(`error`)と、ログ(`/var/log/llm-launchpad-bootstrap.log`)を見るコマンドを出す
* 使える状態になったら、`terraform output` から接続のしかた(エンドポイント、SSM のポートフォワードのコマンド、curl の例、OpenAI 互換 API の例)と自動停止の設定を出す。OpenAI の Python SDK の例は画面で作る。output は起動ごとに1回だけ読む
* サーバーを起動し直すと apply の記録は消えるが、tfvars と SSM パラメータから段階を出せる

### 環境の一覧

terraform のワークスペースのうち、state にリソースがあるものと、画面から起動したもの(tfvars を書いたもの)を出す。

* 環境ごとに、モデル、インスタンス型と購入方法、リージョン、インスタンスの状態、動かし始めてからの時間、ここまでの料金の目安を出す。インスタンスの状態は `describe_instances`(管理用タグ `Project=llm-launchpad` で探す)から読む
* 料金の目安は、インスタンスを最後に動かし始めてから(`LaunchTime`)の時間に、今の1時間あたりの料金(スポットはその AZ のスポット価格、オンデマンドは Price List API)を掛けたもの。止めていた間、ディスク、データ転送の料金は入れない
* 「停止」と「再開」は、state にあるインスタンスを EC2 の API で止める・動かす。インスタンスに付いた `EnvId` のタグがその環境のものか確かめてから動かす。ディスクとほかのリソースは残る
* 「削除(destroy)」は、確認で環境IDを入れたときだけ、起動したときの tfvars を渡して `init` と `destroy` を実行する。成功したら tfvars も消す。tfvars がない環境(terraform を手で実行して作ったもの)は、画面からは削除できない
* model-store の S3 バケットと EBS ボリュームは、環境とは別の state なので、削除しても残る
* 消し忘れたリソースは、管理用タグ `Project=llm-launchpad` が付いているのに、どの環境の state にもないもの(Resource Groups Tagging API で探す)。model-store と budget のリソース(`Persistent=true`)は除く。state を読めなかった環境と、apply や destroy の途中の環境のリソースは、消し忘れと決めない。探すのは、環境のあるリージョンと既定のリージョン(ap-northeast-1)。IAM のロールなどリージョンのないものは探さない
* 一覧は、タブを開いている間だけ30秒ごと(状態が変わっている途中は5秒ごと)に読み直す。読むたびにワークスペースごとに `terraform state pull` を実行する

### ベンチマーク

起動した環境(インスタンスが動いているもの)で、決まったプロンプトを流して測る。

* 「計測する」を押すと、サーバーが SSM の send-command でインスタンスの `launchpad.py bench <モデル> --runs N` を動かす。インスタンスの中から Ollama に送るので、ネットワークの遅れは入らない。数分かかるので、画面は3秒ごとに計測の状態を読み直す
* プロンプトは3つ(短い説明、長い説明、コード)。温度 0、seed 0、出力は 256 トークンまで。最初の1回はモデルを GPU に載せるためで、数えない
* TTFT は、ストリームで最初のトークンが届くまでの時間。tok/s は、Ollama が返す `eval_count` と `eval_duration` から出す。どちらもプロンプトと周の平均
* VRAM は、計測のあとの nvidia-smi の使用量の合計。見積もり(`sizing.py`)との差も出す
* $/100万トークンは、1時間あたりの料金(スポットはその AZ のスポット価格)を出力の tok/s で割ったもの。リクエストを1つずつ順に送ったときの値
* 結果は `benchmarks/results.jsonl`(`LAUNCHPAD_BENCHMARK_FILE`)に1行ずつ足す。画面から CSV と JSON で保存できる。CSV にはプロンプトごとの結果は入れない
* インスタンスの `launchpad.py` は起動したときに置いたものなので、`bench` を足す前に起動した環境では測れない。作り直すと測れる
* サーバーを動かしている端末の AWS の認証情報に、`ssm:SendCommand` と `ssm:GetCommandInvocation` の権限が要る

#### チャットで試す

* 送り先は terraform output の `ollama_endpoint`。サーバーから Ollama の `/api/chat` に送り、応答と tok/s を出す
* SSM で接続する環境(`access_mode=ssm`)では `http://localhost:11434` なので、サーバーを動かしている端末で `ssm_port_forward_command` を動かしたままにする。つながらないときは、そのコマンドをエラーに出す
* 会話はサーバーに残さない。環境を変えると消える

### VRAM の見積もりは Python でも計算する

* 画面でモデルを選んだときにすぐ推奨の型を出すため、`terraform/modules/sizing` と同じ計算を `server/llm_launchpad_webui/sizing.py` にも書いた
* 2つがずれないよう、`tests/test_sizing.py` が、カタログの全モデル x コンテキスト長(4096 / 8192 / 32768 / 131072)x KVキャッシュの型(f16 / q8_0)を terraform の sizing モジュールでも計算し、結果(インスタンス型、収まる型の並び、見積もりの内訳)が同じかを確かめる。terraform がない環境ではこのテストだけ飛ばす
* 式を変えるときは、両方を直す

### terraform の実行

* Terraform のタブから実行できるのは `version` / `init` / `validate` / `plan` だけ。`apply` は起動の確認(`POST /api/launch`)から、`destroy` は環境の一覧(`POST /api/environments/{環境ID}/destroy`)からだけ実行できる
* 起動では、`default` のワークスペースは使えない。渡せる変数は決めたもの(model、region、context_length、kv_cache_type、model_storage、purchase_option、idle_stop_minutes、max_lifetime_hours、instance_type)だけで、型も確かめる
* 同時に動かすのは1つだけ(`init` は `.terraform` を書き換えるため)。実行中に別のコマンドを頼むと 409 を返す
* ワークスペース名は、環境IDと同じ規則(`default` か、英小文字・数字・ハイフンの20文字以内)だけを受け付ける
* 出力は1行ずつ SSE で送る(`event: log`、最後に `event: end` で終了コード)。各行に行番号を `id` として付けるので、接続が切れてもブラウザ(EventSource)が `Last-Event-ID` を送り、続きから受け取れる
* 実行の記録はサーバーのメモリにだけ持つ。サーバーを止めると消える

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/health` | 動いているか、terraform のディレクトリ、実行できるコマンド |
| GET | `/api/catalog` | 提供元、推論エンジン、選べるコンテキスト長と KV キャッシュの型、インスタンス型の表 |
| GET | `/api/models` | モデルの一覧と見積もり。`?vendor=Alibaba&context_length=32768&kv_cache_type=f16` |
| GET | `/api/estimate` | 1つのモデルの見積もり。`?model=qwen3:8b&context_length=8192` |
| GET | `/api/regions` | アカウントで使えるリージョンと名前 |
| GET | `/api/preflight` | 起動前のチェック。`?instance_type=g4dn.xlarge&region=ap-northeast-1` |
| GET | `/api/region-plan` | リージョンでの料金、AZ、ストレージ方式ごとの料金と起動時間の目安、スポットの placement score。`?model=qwen3:8b&region=ap-northeast-1` |
| GET | `/api/environments` | 画面から起動した環境(tfvars を書いたもの)。新しく起動した順 |
| GET | `/api/environments/{環境ID}/progress` | 起動の進み具合。段階、取得の進み具合、経過時間と残り時間の目安、失敗の理由、使える状態なら接続のしかた |
| GET | `/api/environments/overview` | 環境の一覧(インスタンスの状態、経過時間、料金の目安)と、どの state にもない消し忘れのリソース |
| POST | `/api/environments/{環境ID}/stop` | その環境のインスタンスを止める |
| POST | `/api/environments/{環境ID}/start` | 止めたインスタンスを動かし直す |
| POST | `/api/environments/{環境ID}/destroy` | 起動したときの tfvars を渡して init と destroy を実行する。成功したら tfvars を消す |
| POST | `/api/environments/{環境ID}/benchmark` | インスタンスで計測を始める。`{"runs": 3}`。計測の ID を返す |
| GET | `/api/benchmarks/jobs/{id}` | 計測の状態と結果 |
| GET | `/api/benchmarks` | 測った結果の履歴(新しい順、プロンプトごとの結果を含む) |
| GET | `/api/benchmarks.csv` | 測った結果の履歴の CSV |
| POST | `/api/environments/{環境ID}/chat` | モデルと話す。`{"messages": [{"role": "user", "content": "..."}]}` |
| POST | `/api/launch` | tfvars を書いて init と apply を実行する。`{"workspace": "qwen3-8b", "variables": {"model": "qwen3:8b", ...}}` |
| GET | `/api/workspaces` | `terraform workspace list` の結果 |
| POST | `/api/runs` | コマンドを実行する。`{"command": "plan", "workspace": "qwen-test", "variables": {...}}` |
| GET | `/api/runs` | 実行の一覧(新しい順) |
| GET | `/api/runs/{id}` | 実行の状態と、ここまでの出力 |
| GET | `/api/runs/{id}/events` | 出力の SSE。`?start=N` で N 行目から |

## テスト

```sh
cd webui/server && uv run pytest        # 偽の terraform(tests/fake_terraform.py)を使う
cd webui/web && npm test && npm run typecheck
```

## 設定(環境変数)

| 変数 | 既定 | 説明 |
|---|---|---|
| `LAUNCHPAD_CATALOG_DIR` | `catalog/` | モデル定義とインスタンス型の表のディレクトリ |
| `LAUNCHPAD_TERRAFORM_DIR` | `terraform/` | terraform を実行するディレクトリ |
| `LAUNCHPAD_TERRAFORM_BIN` | `terraform` | terraform のコマンド |
| `LAUNCHPAD_WEB_DIST` | `webui/web/dist` | 配る画面のディレクトリ |
| `LAUNCHPAD_BENCHMARK_FILE` | `benchmarks/results.jsonl` | ベンチマークの結果を足していくファイル |
