"""terraform を子プロセスで動かし、出力を行ごとにためて、読み手(SSE)に渡す。

UI は tfvars を作って terraform を呼ぶだけの薄い層にする。画面から渡した変数は
terraform/tfvars/<ワークスペース>.tfvars.json に書き、-var-file で渡すので、同じファイルを使えば
terraform を手で実行しても同じ結果になる。
"""

import asyncio
import datetime
import json
import os
import re
import uuid
from dataclasses import dataclass, field

# 画面から実行できるコマンド。apply は起動の確認(/api/launch)からだけ動かし、ここには入れない
COMMANDS = {
    "version": ["version"],
    "init": ["init", "-input=false", "-no-color"],
    "validate": ["validate", "-no-color"],
    "plan": ["plan", "-input=false", "-no-color"],
}

# 変数ファイルを渡せるコマンド
ACCEPTS_VAR_FILE = {"plan"}

# 起動(/api/launch)で tfvars に書ける変数と、その型。決めたもの以外は書かない
LAUNCH_VARIABLES = {
    "model": str,
    "region": str,
    "context_length": int,
    "kv_cache_type": str,
    "model_storage": str,
    "purchase_option": str,
    "idle_stop_minutes": int,
    "max_lifetime_hours": int,
    "instance_type": str,
}

# 環境ID と同じ規則(terraform/main.tf の time_static.created の precondition)。default はそのまま使える
WORKSPACE_PATTERN = re.compile(r"^(default|[a-z0-9][a-z0-9-]{0,19})$")


class RunnerError(Exception):
    pass


class BusyError(RunnerError):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


@dataclass
class Run:
    id: str
    command: str
    workspace: str
    args: list[str]
    # 順に実行するコマンド。1つでも失敗したら、そこで止める。1つだけなら args と同じ
    steps: list[list[str]] = field(default_factory=list)
    status: str = "running"  # running / succeeded / failed
    exit_code: int | None = None
    started_at: str = field(default_factory=now)
    finished_at: str | None = None
    lines: list[str] = field(default_factory=list)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition, repr=False)

    def summary(self):
        return {
            "id": self.id,
            "command": self.command,
            "workspace": self.workspace,
            "args": self.args,
            "steps": self.steps,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "line_count": len(self.lines),
        }


class Runner:
    def __init__(self, terraform_dir, terraform_bin="terraform"):
        self.terraform_dir = os.path.abspath(terraform_dir)
        self.terraform_bin = terraform_bin
        self.runs: dict[str, Run] = {}
        self._tasks: set[asyncio.Task] = set()

    def var_file_path(self, workspace):
        return os.path.join(self.terraform_dir, "tfvars", f"{workspace}.tfvars.json")

    def write_var_file(self, workspace, variables):
        """画面で選んだ値を tfvars に書く。terraform plan -var-file=tfvars/<ワークスペース>.tfvars.json で同じことができる"""
        path = self.var_file_path(workspace)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(variables, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return path

    def read_var_file(self, workspace):
        """書いた tfvars。なければ None"""
        try:
            with open(self.var_file_path(workspace), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def launched_environments(self):
        """tfvars を書いた環境(画面から起動したもの)。新しく書いた順"""
        directory = os.path.join(self.terraform_dir, "tfvars")
        try:
            entries = [e for e in os.scandir(directory) if e.name.endswith(".tfvars.json")]
        except FileNotFoundError:
            return []
        names = [e.name.removesuffix(".tfvars.json") for e in sorted(entries, key=lambda e: e.stat().st_mtime, reverse=True)]
        return [n for n in names if WORKSPACE_PATTERN.match(n) and n != "default"]

    def latest_launch(self, workspace):
        """そのワークスペースで最後に始めた起動。サーバーを起動し直すと消える"""
        return self.latest_run(workspace, ("launch",))

    async def _read_only(self, workspace, *args):
        """state を読むだけのコマンド。ロックを取らないので、ほかのコマンドの実行中でも動かせる"""
        env = {**os.environ, "TF_WORKSPACE": workspace, "TF_IN_AUTOMATION": "1"}
        proc = await asyncio.create_subprocess_exec(
            self.terraform_bin, f"-chdir={self.terraform_dir}", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL, env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RunnerError(stderr.decode("utf-8", errors="replace").strip() or f"terraform {args[0]} に失敗しました")
        return stdout

    async def outputs(self, workspace):
        """terraform output -json の値"""
        stdout = await self._read_only(workspace, "output", "-json", "-no-color")
        try:
            return {name: o.get("value") for name, o in json.loads(stdout or b"{}").items()}
        except (json.JSONDecodeError, AttributeError) as e:
            raise RunnerError(f"terraform output の結果を読めませんでした: {e}") from e

    async def state(self, workspace):
        """terraform state pull の結果。まだ apply していないワークスペースでは None"""
        stdout = await self._read_only(workspace, "state", "pull")
        if not stdout.strip():
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as e:
            raise RunnerError(f"terraform state pull の結果を読めませんでした: {e}") from e

    def latest_run(self, workspace, commands=("launch", "destroy")):
        """そのワークスペースで最後に始めた起動か削除"""
        found = [r for r in self.runs.values() if r.command in commands and r.workspace == workspace]
        return found[-1] if found else None

    def running(self):
        return [r for r in self.runs.values() if r.status == "running"]

    async def start(self, command, workspace="default", variables=None):
        if command not in COMMANDS:
            raise RunnerError(f"実行できないコマンドです: {command}")
        if not WORKSPACE_PATTERN.match(workspace):
            raise RunnerError("ワークスペース名は default か、英小文字・数字・ハイフンの20文字以内にしてください")
        if variables is not None and command not in ACCEPTS_VAR_FILE:
            raise RunnerError(f"{command} には変数を渡せません")
        # init は .terraform を書き換えるため、同時に動かすのは1つだけにする
        if self.running():
            raise BusyError("別のコマンドが実行中です。終わってから実行してください")

        args = list(COMMANDS[command])
        if variables is not None:
            path = self.write_var_file(workspace, variables)
            args.append(f"-var-file={os.path.relpath(path, self.terraform_dir)}")

        return self._spawn(Run(id=uuid.uuid4().hex[:12], command=command, workspace=workspace, args=args, steps=[args]))

    async def launch(self, workspace, variables):
        """起動の確認で Yes を押したとき。tfvars を書き、init と apply を順に実行する"""
        if not WORKSPACE_PATTERN.match(workspace) or workspace == "default":
            raise RunnerError("環境ID は、英小文字・数字・ハイフンの20文字以内で、default 以外にしてください")
        unknown = sorted(set(variables) - set(LAUNCH_VARIABLES))
        if unknown:
            raise RunnerError(f"起動では渡せない変数です: {', '.join(unknown)}")
        for name, typ in LAUNCH_VARIABLES.items():
            if name in variables and variables[name] is not None and not (
                isinstance(variables[name], typ) and not isinstance(variables[name], bool)
            ):
                raise RunnerError(f"{name} の値の型が違います")
        if "model" not in variables:
            raise RunnerError("model は必須です")
        if self.running():
            raise BusyError("別のコマンドが実行中です。終わってから実行してください")

        path = self.write_var_file(workspace, {k: v for k, v in variables.items() if v is not None})
        var_file = f"-var-file={os.path.relpath(path, self.terraform_dir)}"
        apply_args = ["apply", "-input=false", "-no-color", "-auto-approve", var_file]
        # ワークスペースは TF_WORKSPACE で選ぶ。まだない名前でも、apply がその名前の state を作る
        steps = [list(COMMANDS["init"]), apply_args]
        return self._spawn(Run(id=uuid.uuid4().hex[:12], command="launch", workspace=workspace, args=apply_args, steps=steps))

    async def destroy(self, workspace):
        """環境の削除。起動したときの tfvars を使って init と destroy を順に実行し、終わったら tfvars を消す"""
        if not WORKSPACE_PATTERN.match(workspace) or workspace == "default":
            raise RunnerError("環境ID は、英小文字・数字・ハイフンの20文字以内で、default 以外にしてください")
        if not os.path.exists(self.var_file_path(workspace)):
            raise RunnerError(
                f"tfvars/{workspace}.tfvars.json がないため、画面からは削除できません。"
                f"起動したときの変数を渡して、TF_WORKSPACE={workspace} terraform destroy を実行してください"
            )
        if self.running():
            raise BusyError("別のコマンドが実行中です。終わってから実行してください")

        var_file = f"-var-file={os.path.relpath(self.var_file_path(workspace), self.terraform_dir)}"
        destroy_args = ["destroy", "-input=false", "-no-color", "-auto-approve", var_file]
        steps = [list(COMMANDS["init"]), destroy_args]
        return self._spawn(Run(id=uuid.uuid4().hex[:12], command="destroy", workspace=workspace, args=destroy_args, steps=steps))

    def _spawn(self, run):
        self.runs[run.id] = run
        task = asyncio.create_task(self._execute(run))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _append(self, run, line):
        async with run.changed:
            run.lines.append(line)
            run.changed.notify_all()

    async def _execute(self, run):
        env = {**os.environ, "TF_WORKSPACE": run.workspace, "TF_IN_AUTOMATION": "1"}
        exit_code = 0
        for args in run.steps:
            if len(run.steps) > 1:
                # 複数のコマンドを続けて実行するときは、どこからどのコマンドかが分かるようにする
                await self._append(run, f"$ terraform {' '.join(args)}")
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.terraform_bin, f"-chdir={self.terraform_dir}", *args,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    stdin=asyncio.subprocess.DEVNULL, env=env,
                )
                async for raw in proc.stdout:
                    await self._append(run, raw.decode("utf-8", errors="replace").rstrip("\n"))
                exit_code = await proc.wait()
            except (OSError, asyncio.CancelledError) as e:
                await self._append(run, f"terraform を実行できませんでした: {e}")
                exit_code = -1
            if exit_code != 0:
                break
        if run.command == "destroy" and exit_code == 0:
            # 消した環境は一覧に出さない。同じ環境ID でまた起動すれば、そのときに書き直す
            try:
                os.remove(self.var_file_path(run.workspace))
            except FileNotFoundError:
                pass
        async with run.changed:
            run.exit_code = exit_code
            run.status = "succeeded" if exit_code == 0 else "failed"
            run.finished_at = now()
            run.changed.notify_all()

    async def follow(self, run, start=0):
        """start 行目から、行が増えるたびに返す。コマンドが終わって全部の行を返したら止まる"""
        index = start
        while True:
            async with run.changed:
                await run.changed.wait_for(lambda: len(run.lines) > index or run.status != "running")
                new_lines = run.lines[index:]
                done = run.status != "running"
            for line in new_lines:
                yield line
            index += len(new_lines)
            if done and index >= len(run.lines):
                return


def list_workspaces(terraform_dir, terraform_bin="terraform"):
    """terraform workspace list の結果。今選ばれているものに * が付く"""
    import subprocess

    result = subprocess.run(
        [terraform_bin, f"-chdir={terraform_dir}", "workspace", "list"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RunnerError(result.stderr.strip() or "terraform workspace list に失敗しました")
    workspaces = []
    for line in result.stdout.splitlines():
        name = line.strip().lstrip("*").strip()
        if name:
            workspaces.append({"name": name, "current": line.strip().startswith("*")})
    return workspaces
