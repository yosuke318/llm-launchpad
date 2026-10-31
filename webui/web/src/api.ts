// サーバー(webui/server)の API

import i18n from "./i18n";

export const COMMANDS = ["version", "init", "validate", "plan"] as const;
export type Command = (typeof COMMANDS)[number];

export type RunStatus = "running" | "succeeded" | "failed";

export interface RunSummary {
  id: string;
  // launch は起動の確認から(init と apply を続けて実行する)、destroy は環境の一覧から(init と destroy)
  command: Command | "launch" | "destroy";
  workspace: string;
  args: string[];
  steps: string[][];
  status: RunStatus;
  exit_code: number | null;
  started_at: string;
  finished_at: string | null;
  line_count: number;
}

export interface Workspace {
  name: string;
  current: boolean;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    // エラーや警告の文言を、表示している言語で返してもらう
    headers: { "Content-Type": "application/json", "Accept-Language": i18n.language, ...init?.headers },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // 本文が JSON でなければ、ステータスの文言を使う
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export function listWorkspaces() {
  return request<{ workspaces: Workspace[] }>("/api/workspaces").then((r) => r.workspaces);
}

export function startRun(command: Command, workspace: string, variables?: Record<string, unknown>) {
  return request<RunSummary>("/api/runs", {
    method: "POST",
    body: JSON.stringify({ command, workspace, variables }),
  });
}

export function runEventsUrl(runId: string) {
  return `/api/runs/${encodeURIComponent(runId)}/events`;
}

// モデル定義(catalog/)と VRAM の見積もり

export interface InstanceType {
  type: string;
  gpu_model: string;
  gpu_count: number;
  vram_gib_per_gpu: number;
  vcpu: number;
  memory_gib: number;
}

export interface Engine {
  id: string;
  available: boolean;
}

export interface Catalog {
  vendors: string[];
  engines: Engine[];
  context_lengths: number[];
  kv_cache_types: string[];
  defaults: { context_length: number; kv_cache_type: string };
  instance_types: InstanceType[];
}

export interface ModelDefinition {
  tag: string;
  vendor: string;
  name: string;
  params_b: number;
  quantization: string;
  file_size_gb: number;
  max_context: number;
  layers: number;
  attention_layers: number;
  kv_heads: number;
  head_dim: number;
  license: string;
}

export interface VramEstimate {
  weights_gib: number;
  kv_cache_gib: number;
  overhead_gib: number;
  required_gib: number;
  instance_vram_gib: number;
  headroom_gib: number;
  context_length: number;
  kv_cache_type: string;
}

export interface ModelEstimate {
  model: ModelDefinition;
  instance_type: string | null;
  instance: InstanceType | null;
  fitting_instance_types: string[];
  vram_estimate: VramEstimate;
  // 選んだコンテキスト長がモデルの最大を超えたため、最大で見積もった
  context_capped: boolean;
}

// リージョンとストレージ方式

export interface Region {
  code: string;
  name: string;
}

export type StorageId = "s3_cache" | "ebs" | "download";

// public: パブリックサブネット / private: インターネットへの経路がない閉じたネットワーク(terraform の network_mode)
export type NetworkMode = "public" | "private";

export interface StorageOption {
  id: StorageId;
  // 止めている間もかかる保管の月額(S3 や EBS ボリューム)
  storage_monthly_usd: number | null;
  storage_throughput_monthly_usd?: number | null;
  volume_gib?: number;
  volume_throughput_mibps?: number;
  root_volume_gib: number;
  // インスタンスを動かしている間の、ルートボリュームの1時間あたりの料金
  running_disk_hourly_usd: number | null;
  startup_first_sec: number;
  startup_cached_sec: number | null;
  // 使う前の準備(model-store の apply)が済んでいるか
  ready: boolean;
  prerequisite: "bucket" | "volume" | null;
}

export interface RegionPlan {
  region: string;
  model: string;
  instance_type: string;
  // launchable_zones は起動の画面で選べるAZ(サブネットがあって、インスタンス型を提供しているところ)
  availability: { available: boolean | null; availability_zones: string[] | null; launchable_zones: string[] | null };
  prices: {
    on_demand_hourly_usd: number | null;
    spot_hourly_usd_by_az: Record<string, number>;
    spot_min: { availability_zone: string; hourly_usd: number } | null;
    // 閉じたネットワークで足す、SSM 接続用のエンドポイントの1時間あたりの料金
    private_network_hourly_usd: number | null;
  };
  // スポットで起動できる見込み(1〜10)。AWS の見立て
  spot_placement_scores: Record<string, number> | null;
  storage_options: StorageOption[];
  model_store: { bucket: string; bucket_exists: boolean; volumes: { name: string; id: string; availability_zone: string }[] } | null;
  measured: { source: string };
  warnings: string[];
}

export function listRegions() {
  return request<{ regions: Region[]; default: string }>("/api/regions");
}

export function getRegionPlan(params: { model: string; region: string; context_length: number; kv_cache_type: string }) {
  const query = new URLSearchParams({ ...params, context_length: String(params.context_length) });
  return request<RegionPlan>(`/api/region-plan?${query}`);
}

export function getCatalog() {
  return request<Catalog>("/api/catalog");
}

export function listModels(params: { vendor?: string; context_length: number; kv_cache_type: string }) {
  const query = new URLSearchParams({ context_length: String(params.context_length), kv_cache_type: params.kv_cache_type });
  if (params.vendor) query.set("vendor", params.vendor);
  return request<{ models: ModelEstimate[] }>(`/api/models?${query}`).then((r) => r.models);
}

// 起動前の事前チェック

export type CheckStatus = "ok" | "ng" | "unknown";
export type PurchaseOption = "spot" | "on_demand";

export interface PreflightCheck {
  id: "credentials" | "instance_type" | "quota_on_demand" | "quota_spot";
  status: CheckStatus;
  error?: string;
  account?: string;
  arn?: string;
  profile?: string | null;
  availability_zones?: string[];
  option?: PurchaseOption;
  quota_name?: string;
  limit_vcpus?: number;
  used_vcpus?: number | null;
  needed_vcpus?: number | null;
  available_vcpus?: number;
  request_url?: string;
  pending_requests?: { desired_value: number; status: string; created: string }[];
}

export interface Preflight {
  region: string;
  instance_type: string;
  checks: PreflightCheck[];
  // スポット / オンデマンドごとに、起動(確認のポップアップ)に進めるか
  can_proceed: Record<PurchaseOption, boolean>;
}

export function getPreflight(params: { region: string; instance_type: string }) {
  return request<Preflight>(`/api/preflight?${new URLSearchParams(params)}`);
}

// 起動(確認のポップアップで Yes を押したとき)

export interface LaunchVariables {
  model: string;
  region: string;
  context_length: number;
  kv_cache_type: string;
  model_storage: StorageId;
  network_mode: NetworkMode;
  purchase_option: "spot" | "on-demand";
  idle_stop_minutes: number;
  max_lifetime_hours: number;
  // 省くと、インスタンス型を提供しているAZから terraform が選ぶ
  availability_zone?: string;
}

export function launch(workspace: string, variables: LaunchVariables) {
  return request<RunSummary>("/api/launch", { method: "POST", body: JSON.stringify({ workspace, variables }) });
}

// 在庫がなくて作れなかった環境を、AZを変えて起動し直す。null なら terraform が選ぶ
export function relaunch(workspace: string, availabilityZone: string | null) {
  return request<RunSummary>(`/api/environments/${encodeURIComponent(workspace)}/relaunch`, {
    method: "POST",
    body: JSON.stringify({ availability_zone: availabilityZone }),
  });
}

// 起動の進み具合(apply から使える状態になるまで)

export const PROGRESS_STAGES = ["creating", "booting", "installing_ollama", "preparing_storage", "pulling_model", "verifying", "ready"] as const;
export type ProgressStageId = (typeof PROGRESS_STAGES)[number];

export interface ProgressStage {
  id: ProgressStageId;
  status: "done" | "active" | "failed" | "pending";
  started_at: string | null;
  finished_at: string | null;
}

// 使える状態になったときの接続のしかた(terraform output)
export interface Connection {
  instance_id: string | null;
  instance_type: string | null;
  availability_zone: string | null;
  purchase_option: string | null;
  access_mode: "ssm" | "direct" | null;
  ollama_endpoint: string | null;
  ssm_port_forward_command: string | null;
  curl_example: string | null;
  openai_api_example: string | null;
  ssm_session_command: string | null;
  status_command: string | null;
  bootstrap_log_path: string | null;
  auto_stop: { idle_stop_minutes: number; stop_at: string | null; schedule_name: string | null } | null;
}

export interface ProgressError {
  stage: ProgressStageId;
  message: string | null;
  log_path?: string | null;
  terraform_log?: string[];
  // insufficient_capacity は、AZに在庫がなくてインスタンスを作れなかったとき
  code?: "insufficient_capacity";
  instance_type?: string | null;
  availability_zone?: string | null;
  suggested_zones?: string[];
  // 起動し直すときに選べるAZ。EBS永続では出さない
  zones?: string[];
}

export interface Progress {
  workspace: string;
  region: string | null;
  model: string | null;
  model_storage: StorageId;
  purchase_option: string | null;
  run: RunSummary | null;
  // unknown は、起動の記録はあるが状態をまだ読めていない
  state: "running" | "ready" | "failed" | "unknown";
  stage: ProgressStageId | null;
  stages: ProgressStage[];
  pull: { percent: number; completed_bytes: number; total_bytes: number; message: string } | null;
  cache: { hit?: boolean; restore_sec?: number; formatted?: boolean } | null;
  verify: { first_response_sec: number } | null;
  error: ProgressError | null;
  started_at: string | null;
  ready_at: string | null;
  // サーバーの時刻。経過時間を手元の時計のずれに左右されずに出すため
  now: string;
  elapsed_sec: number | null;
  expected_total_sec: number | null;
  remaining_sec: number | null;
  connection: Connection | null;
  warnings: string[];
}

// 画面から起動した環境(tfvars を書いたもの)。新しく起動した順
export function listEnvironments() {
  return request<{ environments: string[] }>("/api/environments").then((r) => r.environments);
}

export function getProgress(workspace: string) {
  return request<Progress>(`/api/environments/${encodeURIComponent(workspace)}/progress`);
}

// 環境の一覧と、どの state にもない消し忘れのリソース

export interface EnvironmentInstance {
  id: string;
  state: "pending" | "running" | "stopping" | "stopped" | "shutting-down";
  instance_type: string;
  availability_zone: string;
  launch_time: string;
  purchase_option: "spot" | "on-demand";
  env_id: string | null;
  region: string;
}

export interface Environment {
  workspace: string;
  region: string;
  model: string | null;
  instance_type: string | null;
  purchase_option: string | null;
  resource_count: number;
  // 起動したときの tfvars があるか。なければ画面からは削除できない
  has_tfvars: boolean;
  instance: EnvironmentInstance | null;
  // インスタンスを最後に動かし始めてから(動いている間だけ)
  elapsed_sec: number | null;
  hourly_usd: number | null;
  cost_usd: number | null;
  run: RunSummary | null;
  state_error: string | null;
}

export interface Leftover {
  region: string;
  arn: string;
  resource_type: string;
  resource_id: string;
  env_id: string | null;
  name: string | null;
  created_at: string | null;
  instance_state?: EnvironmentInstance["state"];
}

export interface EnvironmentsOverview {
  environments: Environment[];
  leftovers: Leftover[];
  regions: string[];
  unchecked_regions: string[];
  now: string;
  warnings: string[];
}

export function getEnvironmentsOverview() {
  return request<EnvironmentsOverview>("/api/environments/overview");
}

export function stopEnvironment(workspace: string) {
  return request<{ instance_id: string; state: string }>(`/api/environments/${encodeURIComponent(workspace)}/stop`, { method: "POST" });
}

export function startEnvironment(workspace: string) {
  return request<{ instance_id: string; state: string }>(`/api/environments/${encodeURIComponent(workspace)}/start`, { method: "POST" });
}

export function destroyEnvironment(workspace: string) {
  return request<RunSummary>(`/api/environments/${encodeURIComponent(workspace)}/destroy`, { method: "POST" });
}

// ベンチマーク(インスタンスで決まったプロンプトを流して測る)とチャット

export interface BenchmarkPromptResult {
  prompt_id: string;
  run: number;
  ttft_sec: number | null;
  total_sec: number;
  prompt_tokens: number;
  prompt_tokens_per_sec: number | null;
  output_tokens: number;
  output_tokens_per_sec: number | null;
  load_sec: number;
}

export interface Benchmark {
  id: string;
  // 前からある結果には入っていない(ollama として扱う)
  engine?: "ollama" | "bedrock";
  measured_at: string;
  workspace: string;
  model: string;
  region: string | null;
  availability_zone: string | null;
  instance_type: string | null;
  gpu: string | null;
  purchase_option: string | null;
  model_storage: string | null;
  context_length: number | null;
  kv_cache_type: string | null;
  runs: number;
  num_predict: number;
  ttft_sec: number | null;
  output_tokens_per_sec: number | null;
  prompt_tokens_per_sec: number | null;
  warmup_load_sec: number | null;
  vram_estimate_gib: number | null;
  vram_measured_gib: number | null;
  vram_diff_gib: number | null;
  vram_total_gib: number | null;
  hourly_usd: number | null;
  usd_per_million_tokens: number | null;
  results: BenchmarkPromptResult[];
}

export interface BenchmarkJob {
  id: string;
  workspace: string;
  model: string;
  runs: number;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  result: Benchmark | null;
  error: string | null;
  warnings: string[];
}

export const BENCHMARKS_CSV_URL = "/api/benchmarks.csv";
export const BENCHMARKS_JSON_URL = "/api/benchmarks";

export function startBenchmark(workspace: string, runs: number) {
  return request<BenchmarkJob>(`/api/environments/${encodeURIComponent(workspace)}/benchmark`, { method: "POST", body: JSON.stringify({ runs }) });
}

export function getBenchmarkJob(id: string) {
  return request<BenchmarkJob>(`/api/benchmarks/jobs/${encodeURIComponent(id)}`);
}

export function listBenchmarks() {
  return request<{ benchmarks: Benchmark[] }>(BENCHMARKS_JSON_URL).then((r) => r.benchmarks);
}

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export interface ChatReply {
  message: ChatMessage;
  total_sec: number;
  output_tokens: number | null;
  output_tokens_per_sec: number | null;
}

export function sendChat(workspace: string, messages: ChatMessage[]) {
  return request<ChatReply>(`/api/environments/${encodeURIComponent(workspace)}/chat`, { method: "POST", body: JSON.stringify({ messages }) });
}

// Bedrock の環境(terraform/bedrock)。GPUインスタンスは起動せず、閉じた VPC の Lambda からエンドポイント経由で呼ぶ

export interface BedrockModel {
  id: string;
  name: string;
  vendor: string;
  region: string;
  input_usd_per_million: number | null;
  output_usd_per_million: number | null;
}

export interface BedrockModels {
  region: string;
  default: string;
  models: BedrockModel[];
  // 環境があるあいだかかる、bedrock-runtime のエンドポイント1つの料金
  endpoint_hourly_usd: number | null;
  warnings: string[];
}

export interface BedrockEnvironment {
  workspace: string;
  model: string;
  model_name: string;
  region: string;
  run: RunSummary | null;
  // apply が終わるまでは null
  function_name: string | null;
  availability_zone: string | null;
  vpc_endpoint_ids: string[] | null;
}

export interface BedrockChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface BedrockChatReply {
  model: string;
  content: string;
  usage: { inputTokens?: number; outputTokens?: number };
  total_sec: number;
}

export function listBedrockModels(region: string) {
  return request<BedrockModels>(`/api/bedrock/models?${new URLSearchParams({ region })}`);
}

export function launchBedrock(workspace: string, variables: { model: string; region: string }) {
  return request<RunSummary>("/api/bedrock/launch", { method: "POST", body: JSON.stringify({ workspace, variables }) });
}

export function listBedrockEnvironments() {
  return request<{ environments: BedrockEnvironment[]; warnings: string[] }>("/api/bedrock/environments");
}

export function sendBedrockChat(workspace: string, messages: BedrockChatMessage[]) {
  return request<BedrockChatReply>(`/api/bedrock/environments/${encodeURIComponent(workspace)}/chat`, { method: "POST", body: JSON.stringify({ messages }) });
}

export function startBedrockBenchmark(workspace: string, runs: number) {
  return request<BenchmarkJob>(`/api/bedrock/environments/${encodeURIComponent(workspace)}/benchmark`, { method: "POST", body: JSON.stringify({ runs }) });
}

export function destroyBedrockEnvironment(workspace: string) {
  return request<RunSummary>(`/api/bedrock/environments/${encodeURIComponent(workspace)}/destroy`, { method: "POST" });
}
