"""WebUI の API。terraform の実行を受け付け、出力を Server-Sent Events で流す。

    cd webui/server && uv run uvicorn llm_launchpad_webui.app:app --host 127.0.0.1 --port 8000

料金がかかる操作ができるため、127.0.0.1 だけで待ち受ける(--host を 0.0.0.0 にしない)。
"""

import asyncio
import json
import os
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .runner import COMMANDS, BusyError, Runner, RunnerError, list_workspaces

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

TERRAFORM_DIR = os.environ.get("LAUNCHPAD_TERRAFORM_DIR", os.path.join(REPO_ROOT, "terraform"))
TERRAFORM_BIN = os.environ.get("LAUNCHPAD_TERRAFORM_BIN", "terraform")
WEB_DIST = os.environ.get("LAUNCHPAD_WEB_DIST", os.path.join(REPO_ROOT, "webui", "web", "dist"))

# 何も流れない間も接続が切れないよう、この秒数ごとに SSE のコメントを送る
HEARTBEAT_SEC = 15


class RunRequest(BaseModel):
    command: Literal[tuple(COMMANDS)]  # type: ignore[valid-type]
    workspace: str = "default"
    # plan に渡す変数。tfvars/<ワークスペース>.tfvars.json に書いて -var-file で渡す
    variables: dict[str, object] | None = None


def create_app(runner: Runner | None = None, web_dist: str | None = WEB_DIST) -> FastAPI:
    app = FastAPI(title="llm-launchpad WebUI")
    app.state.runner = runner or Runner(TERRAFORM_DIR, TERRAFORM_BIN)

    @app.get("/api/health")
    def health():
        r = app.state.runner
        return {"status": "ok", "terraform_dir": r.terraform_dir, "commands": list(COMMANDS)}

    @app.get("/api/workspaces")
    def workspaces():
        r = app.state.runner
        try:
            return {"workspaces": list_workspaces(r.terraform_dir, r.terraform_bin)}
        except RunnerError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.get("/api/runs")
    def runs():
        return {"runs": [run.summary() for run in reversed(list(app.state.runner.runs.values()))]}

    @app.post("/api/runs", status_code=201)
    async def start_run(req: RunRequest):
        try:
            run = await app.state.runner.start(req.command, req.workspace, req.variables)
        except BusyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RunnerError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return run.summary()

    def get_run(run_id):
        run = app.state.runner.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="実行が見つかりません")
        return run

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        run = get_run(run_id)
        return {**run.summary(), "lines": run.lines}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, start: int = 0, last_event_id: str | None = Header(default=None)):
        """出力を SSE で流す。event: log が1行、最後に event: end で終了コードを送る。
        start に読んだ行数を渡すと、続きから受け取れる。ブラウザの EventSource がつなぎ直すときは、
        最後に受け取った id を Last-Event-ID ヘッダーで送ってくるので、その次の行から送る"""
        run = get_run(run_id)
        runner = app.state.runner
        if last_event_id is not None and last_event_id.isdigit():
            start = max(start, int(last_event_id) + 1)

        async def stream():
            index = start
            lines = runner.follow(run, start)
            pending = asyncio.ensure_future(anext(lines, None))
            try:
                while True:
                    done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SEC)
                    if not done:
                        yield ": keep-alive\n\n"
                        continue
                    line = pending.result()
                    if line is None:
                        break
                    yield f"event: log\nid: {index}\ndata: {json.dumps({'line': line}, ensure_ascii=False)}\n\n"
                    index += 1
                    pending = asyncio.ensure_future(anext(lines, None))
            finally:
                pending.cancel()
            end = {"status": run.status, "exit_code": run.exit_code}
            yield f"event: end\ndata: {json.dumps(end)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # ビルドした画面(webui/web/dist)があれば、同じサーバーから配る
    if web_dist and os.path.isdir(web_dist):
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")

    return app


app = create_app()
