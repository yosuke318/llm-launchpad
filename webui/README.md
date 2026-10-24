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

### UI は tfvars を作るだけの薄い層にする

* 画面から渡した変数は `terraform/tfvars/<ワークスペース>.tfvars.json` に書き、`-var-file` で渡す。同じファイルを使えば、terraform を手で実行しても同じ結果になる

  ```sh
  cd terraform
  TF_WORKSPACE=qwen-test terraform plan -var-file=tfvars/qwen-test.tfvars.json
  ```

* `terraform/tfvars/` は自分のIPなどが入ることがあるため、git に入れない
* ワークスペースは `TF_WORKSPACE` で選ぶ。`terraform workspace select` はしないので、手元で選んでいるワークスペースは変わらない

### terraform の実行

* 画面から実行できるのは `version` / `init` / `validate` / `plan` だけ。`apply` / `destroy` は、料金の確認や環境の一覧の画面と一緒に足す
* 同時に動かすのは1つだけ(`init` は `.terraform` を書き換えるため)。実行中に別のコマンドを頼むと 409 を返す
* ワークスペース名は、環境IDと同じ規則(`default` か、英小文字・数字・ハイフンの20文字以内)だけを受け付ける
* 出力は1行ずつ SSE で送る(`event: log`、最後に `event: end` で終了コード)。各行に行番号を `id` として付けるので、接続が切れてもブラウザ(EventSource)が `Last-Event-ID` を送り、続きから受け取れる
* 実行の記録はサーバーのメモリにだけ持つ。サーバーを止めると消える

## API

| メソッド | パス | 内容 |
|---|---|---|
| GET | `/api/health` | 動いているか、terraform のディレクトリ、実行できるコマンド |
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
| `LAUNCHPAD_TERRAFORM_DIR` | `terraform/` | terraform を実行するディレクトリ |
| `LAUNCHPAD_TERRAFORM_BIN` | `terraform` | terraform のコマンド |
| `LAUNCHPAD_WEB_DIST` | `webui/web/dist` | 配る画面のディレクトリ |
