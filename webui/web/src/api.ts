// サーバー(webui/server)の API

export const COMMANDS = ["version", "init", "validate", "plan"] as const;
export type Command = (typeof COMMANDS)[number];

export type RunStatus = "running" | "succeeded" | "failed";

export interface RunSummary {
  id: string;
  command: Command;
  workspace: string;
  args: string[];
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
    headers: { "Content-Type": "application/json", ...init?.headers },
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
  availability: { available: boolean | null; availability_zones: string[] | null };
  prices: {
    on_demand_hourly_usd: number | null;
    spot_hourly_usd_by_az: Record<string, number>;
    spot_min: { availability_zone: string; hourly_usd: number } | null;
  };
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
