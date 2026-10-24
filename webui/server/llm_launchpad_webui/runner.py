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

# 画面から実行できるコマンド。apply / destroy は、料金の確認や環境の一覧の画面と一緒に足す
COMMANDS = {
    "version": ["version"],
    "init": ["init", "-input=false", "-no-color"],
    "validate": ["validate", "-no-color"],
    "plan": ["plan", "-input=false", "-no-color"],
}

# 変数ファイルを渡せるコマンド
ACCEPTS_VAR_FILE = {"plan"}

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

        run = Run(id=uuid.uuid4().hex[:12], command=command, workspace=workspace, args=args)
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
        try:
            proc = await asyncio.create_subprocess_exec(
                self.terraform_bin, f"-chdir={self.terraform_dir}", *run.args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=env,
            )
            async for raw in proc.stdout:
                await self._append(run, raw.decode("utf-8", errors="replace").rstrip("\n"))
            exit_code = await proc.wait()
        except (OSError, asyncio.CancelledError) as e:
            await self._append(run, f"terraform を実行できませんでした: {e}")
            exit_code = -1
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
