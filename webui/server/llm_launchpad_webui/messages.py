"""サーバーが画面に返す文言(エラー、警告)の日本語と英語。

画面は表示している言語を Accept-Language で送ってくる。app.py のミドルウェアがそれを language に入れ、
t() と MessageError は、文字にするときの language で文言を選ぶ。英語(en)で始まれば英語、それ以外は日本語。
AWS や terraform が返す詳しいメッセージは、訳さずに {error} などに入れる。
"""

import contextvars

LANGUAGES = ("ja", "en")
DEFAULT_LANGUAGE = "ja"

language: contextvars.ContextVar[str] = contextvars.ContextVar("language", default=DEFAULT_LANGUAGE)

MESSAGES = {
    # AWS への問い合わせ(aws_info.py)
    "aws.fetch_failed": {
        "ja": "{what} を取れませんでした: {error}",
        "en": "Could not get {what}: {error}",
    },
    "aws.stop_failed": {
        "ja": "{instance_id} を止められませんでした: {error}",
        "en": "Could not stop {instance_id}: {error}",
    },
    "aws.start_failed": {
        "ja": "{instance_id} を動かし直せませんでした: {error}",
        "en": "Could not start {instance_id}: {error}",
    },
    "aws.command_failed": {
        "ja": "{instance_id} でコマンドを動かせませんでした: {error}",
        "en": "Could not run the command on {instance_id}: {error}",
    },
    "aws.command_timeout": {
        "ja": "{instance_id} のコマンドが {sec} 秒たっても終わりませんでした",
        "en": "The command on {instance_id} did not finish in {sec} seconds",
    },
    # ベンチマークとチャット(benchmark.py、app.py)
    "bench.failed": {
        "ja": "ベンチマークが失敗しました({status}、終了コード {code}): {stderr}",
        "en": "The benchmark failed ({status}, exit code {code}): {stderr}",
    },
    "bench.no_output": {
        "ja": "ベンチマークの結果を読めませんでした。インスタンスの launchpad.py が古いときは、環境を作り直してください",
        "en": "Could not read the benchmark result. If launchpad.py on the instance is old, recreate the environment",
    },
    "bench.unexpected": {
        "ja": "計測の途中で、想定していないエラーが起きました: {error}",
        "en": "An unexpected error occurred during the benchmark: {error}",
    },
    "bench.not_running": {
        "ja": "環境 {workspace} のインスタンスが動いていません(状態: {state})",
        "en": "The instance of environment {workspace} is not running (state: {state})",
    },
    "bench.busy": {
        "ja": "環境 {workspace} は計測中です。終わってから実行してください",
        "en": "Environment {workspace} is being measured. Run this after it finishes",
    },
    "bench.runs_range": {
        "ja": "runs は 1〜{max} にしてください",
        "en": "runs must be between 1 and {max}",
    },
    "bench.job_not_found": {
        "ja": "計測が見つかりません",
        "en": "The benchmark was not found",
    },
    "chat.no_endpoint": {
        "ja": "環境 {workspace} の接続先(terraform output の ollama_endpoint)を読めませんでした: {error}",
        "en": "Could not read the endpoint of environment {workspace} (ollama_endpoint in terraform output): {error}",
    },
    "chat.unreachable": {
        "ja": "{endpoint} に接続できませんでした。SSM で接続するときは、別の端末でポートフォワードを動かしたままにしてください({command}): {error}",
        "en": "Could not connect to {endpoint}. When you connect through SSM, keep the port forward running in another terminal ({command}): {error}",
    },
    "chat.failed": {
        "ja": "応答を受け取れませんでした: {error}",
        "en": "Could not get a response: {error}",
    },
    # モデル定義と見積もり(sizing.py、app.py)
    "model.unknown": {
        "ja": "model \"{model}\" はモデル定義にありません",
        "en": "model \"{model}\" is not in the model definitions",
    },
    "kv_cache_type.invalid": {
        "ja": "kv_cache_type は {choices} のどれかを指定してください",
        "en": "kv_cache_type must be one of {choices}",
    },
    "context_length.range": {
        "ja": "context_length は 512〜{max}({model} の最大コンテキスト長)の範囲で指定してください",
        "en": "context_length must be between 512 and {max} (the maximum context length of {model})",
    },
    "context_length.min": {
        "ja": "context_length は 512 以上にしてください",
        "en": "context_length must be 512 or more",
    },
    "instance_type.none_fits": {
        "ja": "このモデルが収まるインスタンス型がありません",
        "en": "No instance type can fit this model",
    },
    "vcpus.unknown": {
        "ja": "vCPU の数が分かりません",
        "en": "The number of vCPUs is unknown",
    },
    # terraform の実行(runner.py)
    "terraform.stderr": {
        "ja": "{text}",
        "en": "{text}",
    },
    "terraform.command_failed": {
        "ja": "terraform {command} に失敗しました",
        "en": "terraform {command} failed",
    },
    "terraform.parse_failed": {
        "ja": "terraform {command} の結果を読めませんでした: {error}",
        "en": "Could not read the result of terraform {command}: {error}",
    },
    "terraform.exec_failed": {
        "ja": "terraform を実行できませんでした: {error}",
        "en": "Could not run terraform: {error}",
    },
    "command.unknown": {
        "ja": "実行できないコマンドです: {command}",
        "en": "This command cannot be run: {command}",
    },
    "command.no_variables": {
        "ja": "{command} には変数を渡せません",
        "en": "Variables cannot be passed to {command}",
    },
    "workspace.invalid": {
        "ja": "ワークスペース名は default か、英小文字・数字・ハイフンの20文字以内にしてください",
        "en": "The workspace name must be default, or up to 20 lowercase letters, digits, and hyphens",
    },
    "env_id.invalid": {
        "ja": "環境ID は、英小文字・数字・ハイフンの20文字以内で、default 以外にしてください",
        "en": "The environment ID must be up to 20 lowercase letters, digits, and hyphens, and not default",
    },
    "busy": {
        "ja": "別のコマンドが実行中です。終わってから実行してください",
        "en": "Another command is running. Run this after it finishes",
    },
    "launch.unknown_variables": {
        "ja": "起動では渡せない変数です: {names}",
        "en": "These variables cannot be passed to a launch: {names}",
    },
    "launch.wrong_type": {
        "ja": "{name} の値の型が違います",
        "en": "The value of {name} has the wrong type",
    },
    "launch.model_required": {
        "ja": "model は必須です",
        "en": "model is required",
    },
    "destroy.no_tfvars": {
        "ja": "tfvars/{workspace}.tfvars.json がないため、画面からは削除できません。"
              "起動したときの変数を渡して、TF_WORKSPACE={workspace} terraform destroy を実行してください",
        "en": "tfvars/{workspace}.tfvars.json does not exist, so it cannot be deleted from this screen. "
              "Pass the variables used for the launch and run TF_WORKSPACE={workspace} terraform destroy",
    },
    "apply.failed": {
        "ja": "terraform apply が失敗しました(終了コード {code})",
        "en": "terraform apply failed (exit code {code})",
    },
    # 環境と実行(app.py)
    "env.invalid": {
        "ja": "環境ID が正しくありません",
        "en": "The environment ID is not valid",
    },
    "env.no_record": {
        "ja": "環境 {workspace} を起動した記録がありません",
        "en": "There is no record of launching environment {workspace}",
    },
    "env.no_instance": {
        "ja": "環境 {workspace} にはインスタンスがありません",
        "en": "Environment {workspace} has no instance",
    },
    "env.instance_not_found": {
        "ja": "環境 {workspace} のインスタンスが AWS に見つかりません",
        "en": "The instance of environment {workspace} was not found in AWS",
    },
    "env.state_read_failed": {
        "ja": "{workspace} の state を読めませんでした: {error}",
        "en": "Could not read the state of {workspace}: {error}",
    },
    "state.read_failed": {
        "ja": "state を読めませんでした: {error}",
        "en": "Could not read the state: {error}",
    },
    "output.read_failed": {
        "ja": "terraform output を読めませんでした: {error}",
        "en": "Could not read terraform output: {error}",
    },
    "workspace_list.failed": {
        "ja": "terraform workspace list に失敗しました: {error}",
        "en": "terraform workspace list failed: {error}",
    },
    "run.not_found": {
        "ja": "実行が見つかりません",
        "en": "The run was not found",
    },
}


def parse_accept_language(header):
    """Accept-Language の最初の言語。英語なら en、それ以外(ないときも)は ja"""
    first = (header or "").split(",")[0].strip().lower()
    return "en" if first.startswith("en") else DEFAULT_LANGUAGE


def t(key, **params):
    """今の言語の文言。params は文字にしてから入れる(MessageError なら、それも今の言語で出る)"""
    return MESSAGES[key][language.get()].format(**{k: str(v) for k, v in params.items()})


class MessageError(Exception):
    """文言の key と値を持つエラー。str() にしたときの言語で文言を出す"""

    def __init__(self, key, **params):
        if key not in MESSAGES:
            raise KeyError(f"文言がありません: {key}")
        super().__init__(key)
        self.key = key
        self.params = params

    def __str__(self):
        return t(self.key, **self.params)
