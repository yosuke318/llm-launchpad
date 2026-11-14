import type { ComparisonConfig, ComparisonConfigStatus, NetworkMode, Preflight, RegionPlan, StorageId } from "./api";
import { hourlyTotal, networkHourly } from "./cost";

// 比較モードの画面で使う計算。同じモデルを複数の構成で順に起動して測る

// サーバーの MIN_CONFIGS / MAX_CONFIGS と同じ
export const MIN_CONFIGS = 2;
export const MAX_CONFIGS = 6;

// 比較が終わるまで読み直す間隔(ミリ秒)。1つの構成に10分以上かかる
export const COMPARISON_POLL_MS = 5000;

// 時間の目安。計測は、モデルを GPU に載せる1回と、3つのプロンプト × 周(1回 256 トークンまで)
export const BENCH_BASE_SEC = 120;
export const BENCH_PER_RUN_SEC = 60;
// terraform destroy にかかる時間の目安
export const DESTROY_SEC = 180;

export const STORAGES: StorageId[] = ["s3_cache", "ebs", "download"];
export const PURCHASE_OPTIONS: ComparisonConfig["purchase_option"][] = ["spot", "on-demand"];

export function configKey(c: ComparisonConfig) {
  return `${c.instance_type}|${c.model_storage}|${c.purchase_option}`;
}

// 最初に並べる構成。収まるインスタンス型の小さいほうから2つを、スポット・S3 キャッシュで比べる
// S3 キャッシュはどちらのネットワークでも選べる
export function defaultConfigs(fitting: string[]): ComparisonConfig[] {
  return fitting.slice(0, MIN_CONFIGS).map((instance_type) => ({ instance_type, model_storage: "s3_cache", purchase_option: "spot" }));
}

// 次に足す構成。まだない組み合わせのうち、表の上のほうから選ぶ
export function nextConfig(configs: ComparisonConfig[], fitting: string[], networkMode: NetworkMode): ComparisonConfig | null {
  const used = new Set(configs.map(configKey));
  for (const purchase_option of PURCHASE_OPTIONS) {
    for (const model_storage of STORAGES) {
      if (!storageAllowedFor(model_storage, networkMode)) continue;
      for (const instance_type of fitting) {
        const c = { instance_type, model_storage, purchase_option };
        if (!used.has(configKey(c))) return c;
      }
    }
  }
  return null;
}

function storageAllowedFor(storage: StorageId, networkMode: NetworkMode) {
  return networkMode === "public" || storage !== "download";
}

export type ConfigProblem = "duplicate" | "storageNotReady" | "downloadPrivate" | "checkFailed" | "notFit" | "notOffered";

// 構成ごとに、始められない理由を返す。plan や preflight を読み込む前は、その分は見ない
export function configProblems(
  configs: ComparisonConfig[],
  fitting: string[],
  networkMode: NetworkMode,
  plans: Record<string, RegionPlan | undefined>,
  preflights: Record<string, Preflight | undefined>,
): ConfigProblem[][] {
  const counts = new Map<string, number>();
  for (const c of configs) counts.set(configKey(c), (counts.get(configKey(c)) ?? 0) + 1);
  return configs.map((c) => {
    const problems: ConfigProblem[] = [];
    if ((counts.get(configKey(c)) ?? 0) > 1) problems.push("duplicate");
    if (!fitting.includes(c.instance_type)) problems.push("notFit");
    if (!storageAllowedFor(c.model_storage, networkMode)) problems.push("downloadPrivate");
    const plan = plans[c.instance_type];
    if (plan?.availability.available === false) problems.push("notOffered");
    const option = plan?.storage_options.find((o) => o.id === c.model_storage);
    if (option && !option.ready) problems.push("storageNotReady");
    const preflight = preflights[c.instance_type];
    if (preflight && !preflight.can_proceed[c.purchase_option === "spot" ? "spot" : "on_demand"]) problems.push("checkFailed");
    return problems;
  });
}

// 1つの構成の、1時間あたりの目安。スポットは一番安いAZ(AZ は terraform が選ぶので目安)
export function configHourly(c: ComparisonConfig, plan: RegionPlan | undefined, networkMode: NetworkMode) {
  const option = plan?.storage_options.find((o) => o.id === c.model_storage);
  if (!plan || !option) return null;
  const instance = c.purchase_option === "spot" ? plan.prices.spot_min?.hourly_usd : plan.prices.on_demand_hourly_usd;
  return hourlyTotal(instance, option, networkHourly(plan, networkMode));
}

// 起動を始めてから削除し終えるまでの目安(秒)。モデルの取得は初回の目安(S3 や EBS にあれば早く終わる)
export function configDurationSec(c: ComparisonConfig, plan: RegionPlan | undefined, runs: number) {
  const option = plan?.storage_options.find((o) => o.id === c.model_storage);
  if (!option) return null;
  return option.startup_first_sec + BENCH_BASE_SEC + BENCH_PER_RUN_SEC * runs + DESTROY_SEC;
}

// 全部の構成の、時間と料金の目安。1つでも分からなければ料金は null
export function comparisonEstimate(
  configs: ComparisonConfig[],
  plans: Record<string, RegionPlan | undefined>,
  networkMode: NetworkMode,
  runs: number,
) {
  let totalSec = 0;
  let totalUsd: number | null = 0;
  for (const c of configs) {
    const plan = plans[c.instance_type];
    const sec = configDurationSec(c, plan, runs);
    const hourly = configHourly(c, plan, networkMode);
    if (sec == null) return { totalSec: null, totalUsd: null };
    totalSec += sec;
    totalUsd = hourly == null || totalUsd == null ? null : totalUsd + (hourly * sec) / 3600;
  }
  return { totalSec, totalUsd };
}

// 動いている途中の状態か
export function isActive(status: ComparisonConfigStatus) {
  return status === "launching" || status === "starting" || status === "benchmarking" || status === "destroying";
}

// 比較表で一番よい値(TTFT は小さいほど、tok/s は大きいほど、$/100万トークンは小さいほどよい)
export function bestValue(values: (number | null | undefined)[], better: "lower" | "higher") {
  const present = values.filter((v): v is number => v != null);
  if (!present.length) return null;
  return better === "lower" ? Math.min(...present) : Math.max(...present);
}
