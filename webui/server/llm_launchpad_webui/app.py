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

from .aws_info import AwsInfo, AwsInfoError
from .runner import COMMANDS, BusyError, Runner, RunnerError, list_workspaces
from .storage import MEASURED, storage_options
from .sizing import (
    DEFAULT_CONTEXT_LENGTH,
    DEFAULT_KV_CACHE_TYPE,
    KV_CACHE_BYTES,
    SizingError,
    estimate,
    load_catalog,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

CATALOG_DIR = os.environ.get("LAUNCHPAD_CATALOG_DIR", os.path.join(REPO_ROOT, "catalog"))
TERRAFORM_DIR = os.environ.get("LAUNCHPAD_TERRAFORM_DIR", os.path.join(REPO_ROOT, "terraform"))
TERRAFORM_BIN = os.environ.get("LAUNCHPAD_TERRAFORM_BIN", "terraform")
WEB_DIST = os.environ.get("LAUNCHPAD_WEB_DIST", os.path.join(REPO_ROOT, "webui", "web", "dist"))

# 何も流れない間も接続が切れないよう、この秒数ごとに SSE のコメントを送る
HEARTBEAT_SEC = 15

# 画面で選べるコンテキスト長。モデルの最大コンテキスト長を超えるものは、そのモデルでは最大に合わせる
CONTEXT_LENGTHS = [4096, 8192, 16384, 32768, 65536, 131072, 262144]

# 推論エンジン。初期版は Ollama だけ(vLLM は後続で足す)
ENGINES = [{"id": "ollama", "available": True}, {"id": "vllm", "available": False}]

# 既定のリージョン(terraform/variables.tf と同じ)
DEFAULT_REGION = "ap-northeast-1"


class RunRequest(BaseModel):
    command: Literal[tuple(COMMANDS)]  # type: ignore[valid-type]
    workspace: str = "default"
    # plan に渡す変数。tfvars/<ワークスペース>.tfvars.json に書いて -var-file で渡す
    variables: dict[str, object] | None = None


def create_app(runner: Runner | None = None, web_dist: str | None = WEB_DIST, catalog_dir: str = CATALOG_DIR,
               aws_info: AwsInfo | None = None) -> FastAPI:
    app = FastAPI(title="llm-launchpad WebUI")
    app.state.runner = runner or Runner(TERRAFORM_DIR, TERRAFORM_BIN)
    # AWS への問い合わせは、使うときに初めて用意する(認証情報がなくても、ほかの画面は動くように)
    app.state.aws_info = aws_info

    def aws():
        if app.state.aws_info is None:
            app.state.aws_info = AwsInfo()
        return app.state.aws_info

    @app.get("/api/regions")
    def regions():
        try:
            return {"regions": aws().regions(), "default": DEFAULT_REGION}
        except AwsInfoError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.get("/api/region-plan")
    def region_plan(model: str, region: str = DEFAULT_REGION, context_length: int = DEFAULT_CONTEXT_LENGTH,
                    kv_cache_type: str = DEFAULT_KV_CACHE_TYPE, instance_type: str | None = None):
        """選んだリージョンで、そのモデルの推奨インスタンス型が使えるか、いくらか、ストレージ方式ごとの料金と起動時間の目安。
        料金などは1つ取れなくても止めず、その項目を null にして warnings に理由を残す"""
        try:
            est = estimate(catalog(), model, min(context_length, catalog().models.get(model, {}).get("max_context", context_length)), kv_cache_type)
        except SizingError as e:
            raise HTTPException(status_code=422, detail=str(e))
        itype = instance_type or est["instance_type"]
        if itype is None:
            raise HTTPException(status_code=422, detail="このモデルが収まるインスタンス型がありません")

        info = aws()
        warnings = []

        def get(fn, *args):
            try:
                return fn(*args)
            except AwsInfoError as e:
                warnings.append(str(e))
                return None

        azs = get(info.offering_azs, region, itype)
        on_demand = get(info.on_demand_hourly, region, itype)
        spot = get(info.spot_hourly_by_az, region, itype) or {}
        # スポットは、インスタンス型を提供している AZ の中で一番安いところ
        spot_in_offered = {az: p for az, p in spot.items() if azs is None or az in azs}
        spot_min = min(spot_in_offered.items(), key=lambda kv: kv[1]) if spot_in_offered else None
        model_store = get(info.model_store, region)
        file_size_gb = est["model"]["file_size_gb"]

        return {
            "region": region,
            "model": model,
            "instance_type": itype,
            "availability": {"available": bool(azs) if azs is not None else None, "availability_zones": azs},
            "prices": {
                "on_demand_hourly_usd": on_demand,
                "spot_hourly_usd_by_az": spot_in_offered,
                "spot_min": {"availability_zone": spot_min[0], "hourly_usd": spot_min[1]} if spot_min else None,
            },
            "storage_options": storage_options(
                file_size_gb, get(info.gp3_prices, region), get(info.s3_standard_per_gb_month, region), model_store,
            ),
            "model_store": model_store,
            "measured": MEASURED,
            "warnings": warnings,
        }

    def catalog():
        # catalog/*.yaml をそのまま読む(モデル定義を足したら、サーバーを起動し直すと反映される)
        return load_catalog(catalog_dir)

    @app.get("/api/catalog")
    def catalog_summary():
        """画面の選択肢。提供元はモデル定義に出てくる順"""
        c = catalog()
        vendors = list(dict.fromkeys(m["vendor"] for m in c.models.values()))
        return {
            "vendors": vendors,
            "engines": ENGINES,
            "context_lengths": CONTEXT_LENGTHS,
            "kv_cache_types": list(KV_CACHE_BYTES),
            "defaults": {"context_length": DEFAULT_CONTEXT_LENGTH, "kv_cache_type": DEFAULT_KV_CACHE_TYPE},
            "instance_types": c.instance_types,
        }

    @app.get("/api/models")
    def models(vendor: str | None = None, context_length: int = DEFAULT_CONTEXT_LENGTH,
               kv_cache_type: str = DEFAULT_KV_CACHE_TYPE):
        """モデルの一覧と、それぞれの見積もり。context_length がモデルの最大を超えるときは、最大で見積もる"""
        c = catalog()
        if kv_cache_type not in KV_CACHE_BYTES:
            raise HTTPException(status_code=422, detail=f"kv_cache_type は {' / '.join(KV_CACHE_BYTES)} のどれかを指定してください")
        if context_length < 512:
            raise HTTPException(status_code=422, detail="context_length は 512 以上にしてください")
        result = []
        for tag, m in c.models.items():
            if vendor and m["vendor"] != vendor:
                continue
            ctx = min(context_length, m["max_context"])
            result.append({**estimate(c, tag, ctx, kv_cache_type), "context_capped": ctx < context_length})
        return {"models": result}

    @app.get("/api/estimate")
    def estimate_one(model: str, context_length: int = DEFAULT_CONTEXT_LENGTH, kv_cache_type: str = DEFAULT_KV_CACHE_TYPE):
        try:
            return estimate(catalog(), model, context_length, kv_cache_type)
        except SizingError as e:
            raise HTTPException(status_code=422, detail=str(e))

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
