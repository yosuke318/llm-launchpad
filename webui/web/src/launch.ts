import type { LaunchVariables, ModelEstimate, NetworkMode, RegionPlan, StorageOption } from "./api";
import { hourlyTotal, networkHourly } from "./cost";

// モデルのタグから環境ID(ワークスペース名)の候補を作る。英小文字・数字・ハイフンの20文字以内(terraform の規則)
export function defaultEnvId(tag: string) {
  const id = tag
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 20)
    .replace(/-+$/, "");
  return id || "llm";
}

export const ENV_ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,19}$/;

export function isValidEnvId(id: string) {
  return ENV_ID_PATTERN.test(id) && id !== "default";
}

// Spot placement score(1〜10)を、中断や起動できないリスクの目安に分ける
export type PlacementRisk = "low" | "medium" | "high";

export function placementRisk(score: number): PlacementRisk {
  if (score >= 7) return "low";
  if (score >= 4) return "medium";
  return "high";
}

export function bestPlacement(scores: Record<string, number> | null) {
  if (!scores) return null;
  const entries = Object.entries(scores);
  if (!entries.length) return null;
  const [availabilityZone, score] = entries.reduce((a, b) => (b[1] > a[1] ? b : a));
  return { availabilityZone, score, risk: placementRisk(score) };
}

export interface LaunchSettings {
  purchaseOption: "spot" | "on-demand";
  idleStopMinutes: number;
  maxLifetimeHours: number;
  // null なら、インスタンス型を提供しているAZから terraform が選ぶ
  availabilityZone: string | null;
}

// EBS永続で付けるボリューム(terraform の model_volume_name の既定 default)のAZ。EBS はこのAZにしか付けられない
export function ebsVolumeZone(plan: RegionPlan) {
  return plan.model_store?.volumes.find((v) => v.name === "default")?.availability_zone ?? null;
}

// AZを選んだときの Spot placement score。選ばなければ一番よいAZ
export function zonePlacement(scores: Record<string, number> | null, availabilityZone: string | null) {
  if (availabilityZone && scores?.[availabilityZone] != null) {
    const score = scores[availabilityZone];
    return { availabilityZone, score, risk: placementRisk(score) };
  }
  return bestPlacement(scores);
}

// tfvars に書く値。画面に出した内容と同じものを書く
export function launchVariables(
  estimate: ModelEstimate,
  plan: RegionPlan,
  option: StorageOption,
  settings: LaunchSettings,
  networkMode: NetworkMode = "public",
): LaunchVariables {
  return {
    model: estimate.model.tag,
    region: plan.region,
    context_length: estimate.vram_estimate.context_length,
    kv_cache_type: estimate.vram_estimate.kv_cache_type,
    model_storage: option.id,
    network_mode: networkMode,
    purchase_option: settings.purchaseOption,
    idle_stop_minutes: settings.idleStopMinutes,
    max_lifetime_hours: settings.maxLifetimeHours,
    // EBS永続はボリュームのAZに起動するので書かない
    ...(settings.availabilityZone && option.id !== "ebs" ? { availability_zone: settings.availabilityZone } : {}),
  };
}

// 選んだ購入方法での、1時間あたりの目安。スポットでAZを選んだときは、そのAZの価格。閉じたネットワークではエンドポイントの分を足す
export function launchHourly(
  plan: RegionPlan,
  option: StorageOption,
  purchaseOption: "spot" | "on-demand",
  availabilityZone: string | null = null,
  networkMode: NetworkMode = "public",
) {
  const spot = availabilityZone ? plan.prices.spot_hourly_usd_by_az[availabilityZone] : plan.prices.spot_min?.hourly_usd;
  const instance = purchaseOption === "spot" ? spot : plan.prices.on_demand_hourly_usd;
  return hourlyTotal(instance, option, networkHourly(plan, networkMode));
}

// 閉じたネットワークではインターネットに出られないため、毎回ダウンロードは選べない(terraform の network_mode の検証と同じ)
export function storageAllowed(storage: StorageOption["id"], networkMode: NetworkMode) {
  return networkMode === "public" || storage !== "download";
}

// 自動停止の値が terraform の変数の決まりに合うか(idle_stop_minutes は 0 か 10〜1440、max_lifetime_hours は 0〜720)
export function validIdleStop(minutes: number) {
  return Number.isInteger(minutes) && (minutes === 0 || (minutes >= 10 && minutes <= 1440));
}

export function validMaxLifetime(hours: number) {
  return Number.isInteger(hours) && hours >= 0 && hours <= 720;
}
