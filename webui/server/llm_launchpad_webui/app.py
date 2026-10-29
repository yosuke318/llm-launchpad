"""WebUI の API。terraform の実行を受け付け、出力を Server-Sent Events で流す。

    cd webui/server && uv run uvicorn llm_launchpad_webui.app:app --host 127.0.0.1 --port 8000

料金がかかる操作ができるため、127.0.0.1 だけで待ち受ける(--host を 0.0.0.0 にしない)。
"""

import asyncio
import datetime
import json
import os
import uuid
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .aws_info import AwsInfo, AwsInfoError
from .benchmark import DEFAULT_RUNS, MAX_RUNS, BenchmarkError, BenchmarkStore, bench_command, parse_output, summarize
from .chat import ChatError, ChatUnreachable, chat_once
from .environments import build_environment, find_leftovers, hourly_usd, managed_resources, state_identifiers, state_instance
from .messages import language, parse_accept_language, t
from .runner import COMMANDS, WORKSPACE_PATTERN, BusyError, Runner, RunnerError, list_workspaces
from .preflight import run_preflight
from .progress import build_progress, connection_info
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
# ベンチマークの結果を足していくファイル(JSON Lines)
BENCHMARK_FILE = os.environ.get("LAUNCHPAD_BENCHMARK_FILE", os.path.join(REPO_ROOT, "benchmarks", "results.jsonl"))

# 何も流れない間も接続が切れないよう、この秒数ごとに SSE のコメントを送る
HEARTBEAT_SEC = 15

# 画面で選べるコンテキスト長。モデルの最大コンテキスト長を超えるものは、そのモデルでは最大に合わせる
CONTEXT_LENGTHS = [4096, 8192, 16384, 32768, 65536, 131072, 262144]

# 推論エンジン。初期版は Ollama だけ(vLLM は後続で足す)
ENGINES = [{"id": "ollama", "available": True}, {"id": "vllm", "available": False}]

# 既定のリージョン(terraform/variables.tf と同じ)
DEFAULT_REGION = "ap-northeast-1"


class LaunchRequest(BaseModel):
    # 環境ID(ワークスペース名)。default 以外
    workspace: str
    # tfvars に書く値。runner.LAUNCH_VARIABLES のものだけ
    variables: dict[str, object]


class RunRequest(BaseModel):
    command: Literal[tuple(COMMANDS)]  # type: ignore[valid-type]
    workspace: str = "default"
    # plan に渡す変数。tfvars/<ワークスペース>.tfvars.json に書いて -var-file で渡す
    variables: dict[str, object] | None = None


class BenchmarkRequest(BaseModel):
    # プロンプトを何周流すか
    runs: int = DEFAULT_RUNS


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


def create_app(runner: Runner | None = None, web_dist: str | None = WEB_DIST, catalog_dir: str = CATALOG_DIR,
               aws_info: AwsInfo | None = None, benchmark_file: str = BENCHMARK_FILE) -> FastAPI:
    app = FastAPI(title="llm-launchpad WebUI")
    app.state.runner = runner or Runner(TERRAFORM_DIR, TERRAFORM_BIN)
    # AWS への問い合わせは、使うときに初めて用意する(認証情報がなくても、ほかの画面は動くように)
    app.state.aws_info = aws_info
    # terraform output の結果。apply が終わるまで変わらないので、ワークスペースと起動の実行ごとに1回だけ読む
    app.state.outputs = {}
    # ベンチマークの履歴と、実行中の計測(サーバーのメモリにだけ持つ)
    app.state.benchmarks = BenchmarkStore(benchmark_file)
    app.state.benchmark_jobs = {}
    app.state.tasks = set()
    # チャットの送り先(terraform output)。環境ごとに1回だけ読む
    app.state.chat_outputs = {}

    @app.middleware("http")
    async def select_language(request: Request, call_next):
        """画面が送る Accept-Language で、エラーや警告の文言の言語を選ぶ"""
        token = language.set(parse_accept_language(request.headers.get("accept-language")))
        try:
            return await call_next(request)
        finally:
            language.reset(token)

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

    @app.get("/api/preflight")
    def preflight(instance_type: str, region: str = DEFAULT_REGION):
        """起動前の事前チェック。can_proceed がスポット / オンデマンドごとに、起動に進めるかを表す"""
        catalog_vcpus = next((t["vcpu"] for t in catalog().instance_types if t["type"] == instance_type), None)
        return run_preflight(aws(), region, instance_type, catalog_vcpus)

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
            raise HTTPException(status_code=422, detail=t("instance_type.none_fits"))

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
        placement = get(info.spot_placement_scores, region, itype)
        placement_in_offered = {az: v for az, v in (placement or {}).items() if azs is None or az in azs}
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
            # スポットで起動できる見込み(1〜10)。AWS の見立てで、実際の起動のしやすさとは違うことがある
            "spot_placement_scores": placement_in_offered if placement is not None else None,
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
            raise HTTPException(status_code=422, detail=t("kv_cache_type.invalid", choices=" / ".join(KV_CACHE_BYTES)))
        if context_length < 512:
            raise HTTPException(status_code=422, detail=t("context_length.min"))
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

    @app.post("/api/launch", status_code=201)
    async def launch(req: LaunchRequest):
        """起動の確認で Yes を押したとき。tfvars/<環境ID>.tfvars.json を書き、init と apply を実行する。
        料金がかかるインスタンスを作るので、画面の確認を通したときだけ呼ぶ"""
        model = req.variables.get("model")
        if not isinstance(model, str) or model not in catalog().models:
            raise HTTPException(status_code=422, detail=t("model.unknown", model=model))
        try:
            run = await app.state.runner.launch(req.workspace, req.variables)
        except BusyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RunnerError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return run.summary()

    @app.get("/api/environments")
    def environments():
        """画面から起動した環境(tfvars を書いたもの)。新しく起動した順"""
        return {"environments": app.state.runner.launched_environments()}

    async def read_states(workspaces):
        """ワークスペースごとの state。読めなかったものは errors に理由を入れる"""
        runner = app.state.runner
        results = await asyncio.gather(*(runner.state(ws) for ws in workspaces), return_exceptions=True)
        states, errors = {}, {}
        for ws, result in zip(workspaces, results):
            if isinstance(result, RunnerError):
                errors[ws] = str(result)
            elif isinstance(result, BaseException):
                raise result
            else:
                states[ws] = result
        return states, errors

    @app.get("/api/environments/overview")
    async def environments_overview():
        """環境の一覧(インスタンスの状態、経過時間、料金の目安)と、どの state にもない消し忘れのリソース"""
        runner = app.state.runner
        warnings = []
        try:
            workspaces = [w["name"] for w in await asyncio.to_thread(list_workspaces, runner.terraform_dir, runner.terraform_bin)]
        except RunnerError as e:
            workspaces = []
            warnings.append(t("workspace_list.failed", error=e))
        launched = runner.launched_environments()
        names = list(dict.fromkeys(launched + workspaces))
        states, state_errors = await read_states(names)
        warnings += [t("env.state_read_failed", workspace=ws, error=e) for ws, e in state_errors.items()]

        tfvars = {ws: runner.read_var_file(ws) for ws in names}
        # default は、何か作ってあるときだけ出す
        shown = [ws for ws in names if ws in state_errors or managed_resources(states.get(ws)) or (tfvars[ws] and ws != "default")]

        # 環境のあるリージョンと既定のリージョンを見る(消し忘れもここから探す)
        regions = {DEFAULT_REGION}
        for ws in names:
            attrs = state_instance(states.get(ws)) or {}
            regions.add(attrs.get("region") or (tfvars[ws] or {}).get("region") or DEFAULT_REGION)
        regions = sorted(regions)

        info = aws()

        def get(fn, *args):
            try:
                return fn(*args)
            except AwsInfoError as e:
                warnings.append(str(e))
                return None

        instances, tagged, failed_regions = {}, [], set()
        for region in regions:
            found = get(info.project_instances, region)
            resources = get(info.tagged_resources, region)
            if found is None or resources is None:
                failed_regions.add(region)
            for inst in found or []:
                instances[inst["id"]] = {**inst, "region": region}
            tagged += [{**r, "region": region} for r in resources or []]

        now = datetime.datetime.now(datetime.timezone.utc)
        environments = []
        for ws in shown:
            state = states.get(ws)
            attrs = state_instance(state) or {}
            instance = instances.get(attrs.get("id")) if attrs.get("id") else None
            prices = None
            if instance and instance["state"] == "running":
                region = instance["region"]
                if instance["purchase_option"] == "spot":
                    prices = {"spot_by_az": get(info.spot_hourly_by_az, region, instance["instance_type"])}
                else:
                    prices = {"on_demand": get(info.on_demand_hourly, region, instance["instance_type"])}
            env = build_environment(ws, tfvars[ws], state, instance, runner.latest_run(ws), prices, now, DEFAULT_REGION)
            environments.append({**env, "state_error": state_errors.get(ws)})

        # apply や destroy の途中の環境は、state に書く前のリソースがあるので、消し忘れとは決めない
        busy = {r.workspace for r in runner.running()}
        known = set().union(*(state_identifiers(s) for s in states.values())) if states else set()
        leftovers = find_leftovers(tagged, known, set(instances), skip_env_ids=set(state_errors) | busy)
        for x in leftovers:
            if x["resource_type"] == "ec2:instance":
                x["instance_state"] = instances[x["resource_id"]]["state"]

        return {
            "environments": environments,
            "leftovers": leftovers,
            "regions": regions,
            # 問い合わせに失敗したリージョンでは、消し忘れがあっても出せない
            "unchecked_regions": sorted(failed_regions),
            "now": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "warnings": warnings,
        }

    async def environment_instance(workspace):
        """その環境の state にあるインスタンスを、AWS で見たもの。管理用タグがその環境のものか確かめる"""
        if not WORKSPACE_PATTERN.match(workspace):
            raise HTTPException(status_code=422, detail=t("env.invalid"))
        try:
            state = await app.state.runner.state(workspace)
        except RunnerError as e:
            raise HTTPException(status_code=502, detail=t("state.read_failed", error=e))
        attrs = state_instance(state)
        if not attrs:
            raise HTTPException(status_code=404, detail=t("env.no_instance", workspace=workspace))
        tfvars = app.state.runner.read_var_file(workspace) or {}
        region = attrs.get("region") or tfvars.get("region") or DEFAULT_REGION
        try:
            found = {i["id"]: i for i in aws().project_instances(region)}
        except AwsInfoError as e:
            raise HTTPException(status_code=502, detail=str(e))
        instance = found.get(attrs.get("id"))
        if instance is None or instance["env_id"] != workspace:
            raise HTTPException(status_code=404, detail=t("env.instance_not_found", workspace=workspace))
        return region, instance

    @app.post("/api/environments/{workspace}/stop")
    async def stop_environment(workspace: str):
        """インスタンスを止める。ディスクと、ほかのリソースは残る"""
        region, instance = await environment_instance(workspace)
        try:
            return {"instance_id": instance["id"], "state": aws().stop_instance(region, instance["id"])}
        except AwsInfoError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.post("/api/environments/{workspace}/start")
    async def start_environment(workspace: str):
        """止めたインスタンスを動かし直す"""
        region, instance = await environment_instance(workspace)
        try:
            return {"instance_id": instance["id"], "state": aws().start_instance(region, instance["id"])}
        except AwsInfoError as e:
            raise HTTPException(status_code=502, detail=str(e))

    @app.post("/api/environments/{workspace}/destroy", status_code=201)
    async def destroy_environment(workspace: str):
        """環境を削除する(terraform destroy)。画面で確認を通したときだけ呼ぶ"""
        try:
            run = await app.state.runner.destroy(workspace)
        except BusyError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except RunnerError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return run.summary()

    def benchmark_job_summary(job):
        # エラーは、読んだ人の言語で出す
        return {**{k: v for k, v in job.items() if k != "error"}, "error": None if job["error"] is None else str(job["error"])}

    def run_benchmark(job, region, instance, tfvars):
        """スレッドで動かす。インスタンスで測り、料金と VRAM の見積もりを足して保存する"""
        try:
            res = aws().run_command(region, instance["id"], bench_command(job["model"], job["runs"]))
            if res["status"] != "Success":
                raise BenchmarkError("bench.failed", status=res["status"], code=res["exit_code"], stderr=res["stderr"].strip()[-2000:])
            raw = parse_output(res["stdout"])
            prices = None
            try:
                if instance["purchase_option"] == "spot":
                    prices = {"spot_by_az": aws().spot_hourly_by_az(region, instance["instance_type"])}
                else:
                    prices = {"on_demand": aws().on_demand_hourly(region, instance["instance_type"])}
            except AwsInfoError as e:
                job["warnings"].append(e)
            # tfvars に書いていない値は、terraform の既定と同じものを使ったことになる。見積もりにも結果にも、その値を入れる
            tfvars = {
                **tfvars,
                "context_length": tfvars.get("context_length") or (raw.get("loaded") or {}).get("context_length") or DEFAULT_CONTEXT_LENGTH,
                "kv_cache_type": tfvars.get("kv_cache_type") or DEFAULT_KV_CACHE_TYPE,
            }
            try:
                est = estimate(catalog(), job["model"], tfvars["context_length"], tfvars["kv_cache_type"])
                vram_estimate = est["vram_estimate"]["required_gib"]
            except SizingError as e:
                job["warnings"].append(e)
                vram_estimate = None
            record = summarize(raw, job["workspace"], tfvars, {**instance, "region": region}, hourly_usd(prices, instance), vram_estimate)
            app.state.benchmarks.append(record)
            job.update(status="succeeded", result=record)
        except (AwsInfoError, BenchmarkError) as e:
            job.update(status="failed", error=e)
        except Exception as e:
            # 結果のファイルに書けないときなど。running のまま残すと、画面が待ち続ける
            job.update(status="failed", error=BenchmarkError("bench.unexpected", error=f"{type(e).__name__}: {e}"))
        finally:
            job["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    @app.post("/api/environments/{workspace}/benchmark", status_code=201)
    async def start_benchmark(workspace: str, req: BenchmarkRequest):
        """インスタンスで決まったプロンプトを流して測る。数分かかるので、すぐに計測の ID を返し、画面は読み直して待つ"""
        if not 1 <= req.runs <= MAX_RUNS:
            raise HTTPException(status_code=422, detail=t("bench.runs_range", max=MAX_RUNS))
        if any(j["workspace"] == workspace and j["status"] == "running" for j in app.state.benchmark_jobs.values()):
            raise HTTPException(status_code=409, detail=t("bench.busy", workspace=workspace))
        region, instance = await environment_instance(workspace)
        if instance["state"] != "running":
            raise HTTPException(status_code=409, detail=t("bench.not_running", workspace=workspace, state=instance["state"]))
        tfvars = app.state.runner.read_var_file(workspace) or {}
        model = tfvars.get("model")
        if not model:
            raise HTTPException(status_code=422, detail=t("env.no_record", workspace=workspace))
        job = {
            "id": uuid.uuid4().hex[:12], "workspace": workspace, "model": model, "runs": req.runs, "status": "running",
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "finished_at": None, "result": None, "error": None, "warnings": [],
        }
        app.state.benchmark_jobs[job["id"]] = job
        task = asyncio.create_task(asyncio.to_thread(run_benchmark, job, region, instance, tfvars))
        app.state.tasks.add(task)
        task.add_done_callback(app.state.tasks.discard)
        return {**benchmark_job_summary(job), "warnings": []}

    @app.get("/api/benchmarks/jobs/{job_id}")
    def benchmark_job(job_id: str):
        job = app.state.benchmark_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=t("bench.job_not_found"))
        return {**benchmark_job_summary(job), "warnings": [str(w) for w in job["warnings"]]}

    @app.get("/api/benchmarks")
    def benchmarks():
        """測った結果の履歴(新しい順)。プロンプトごとの結果も入れる"""
        return {"benchmarks": app.state.benchmarks.list()}

    @app.get("/api/benchmarks.csv")
    def benchmarks_csv():
        return Response(app.state.benchmarks.csv(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="benchmarks.csv"'})

    @app.post("/api/environments/{workspace}/chat")
    async def chat(workspace: str, req: ChatRequest):
        """モデルと話す。terraform output の ollama_endpoint に送る(SSM のときは、手元でポートフォワードを動かしておく)"""
        if not WORKSPACE_PATTERN.match(workspace) or workspace == "default":
            raise HTTPException(status_code=422, detail=t("env.invalid"))
        tfvars = app.state.runner.read_var_file(workspace)
        if not tfvars or not tfvars.get("model"):
            raise HTTPException(status_code=404, detail=t("env.no_record", workspace=workspace))
        outputs = app.state.chat_outputs.get(workspace)
        if outputs is None:
            try:
                outputs = await app.state.runner.outputs(workspace)
            except RunnerError as e:
                raise HTTPException(status_code=502, detail=t("chat.no_endpoint", workspace=workspace, error=e))
            app.state.chat_outputs[workspace] = outputs
        endpoint = outputs.get("ollama_endpoint")
        if not endpoint:
            raise HTTPException(status_code=502, detail=t("chat.no_endpoint", workspace=workspace, error="ollama_endpoint"))
        messages = [m.model_dump() for m in req.messages]
        try:
            return await asyncio.to_thread(chat_once, endpoint, tfvars["model"], messages)
        except ChatUnreachable as e:
            raise HTTPException(status_code=502, detail=t("chat.unreachable", endpoint=endpoint,
                                                          command=outputs.get("ssm_port_forward_command") or "-", error=e))
        except ChatError as e:
            raise HTTPException(status_code=502, detail=t("chat.failed", error=e))

    @app.get("/api/environments/{workspace}/progress")
    async def progress(workspace: str):
        """起動の進み具合。apply の実行(サーバーのメモリにある最後の起動)と、インスタンスが SSM パラメータに書く状態を
        段階に並べ直す。使える状態になったら、terraform output から接続のしかたを足す"""
        if not WORKSPACE_PATTERN.match(workspace) or workspace == "default":
            raise HTTPException(status_code=422, detail=t("env.invalid"))
        runner = app.state.runner
        tfvars = runner.read_var_file(workspace)
        run = runner.latest_launch(workspace)
        if tfvars is None and run is None:
            raise HTTPException(status_code=404, detail=t("env.no_record", workspace=workspace))

        warnings = []
        region = (tfvars or {}).get("region", DEFAULT_REGION)
        status = None
        # apply が終わるまでは、前に起動したときの状態が残っていることがあるので読まない
        if run is None or run.status == "succeeded":
            try:
                status = aws().bootstrap_status(region, workspace)
            except AwsInfoError as e:
                warnings.append(str(e))

        model = catalog().models.get((tfvars or {}).get("model"))
        result = build_progress(workspace, tfvars, run, status, model["file_size_gb"] if model else None)

        outputs = None
        if result["state"] in ("ready", "failed") and result["stage"] != "creating":
            key = (workspace, run.id if run else None)
            outputs = app.state.outputs.get(key)
            if outputs is None:
                try:
                    outputs = await runner.outputs(workspace)
                    app.state.outputs[key] = outputs
                except RunnerError as e:
                    warnings.append(t("output.read_failed", error=e))
        return {**result, "connection": connection_info(outputs), "warnings": warnings}

    def get_run(run_id):
        run = app.state.runner.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=t("run.not_found"))
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
