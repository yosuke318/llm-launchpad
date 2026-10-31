import type { NetworkMode, RegionPlan, StorageOption } from "./api";

export const HOURS_PER_MONTH = 730;

// 1時間あたりの料金の目安 = インスタンス + 動かしている間のルートボリューム + 保管の料金(月額 ÷ 730)
// + ネットワークの料金(閉じたネットワークの VPC エンドポイント)
export function hourlyTotal(instanceHourly: number | null | undefined, option: StorageOption, networkHourly: number | null = 0): number | null {
  if (instanceHourly == null || option.running_disk_hourly_usd == null || option.storage_monthly_usd == null || networkHourly == null) return null;
  return instanceHourly + option.running_disk_hourly_usd + option.storage_monthly_usd / HOURS_PER_MONTH + networkHourly;
}

// パブリックサブネットでは 0。閉じたネットワークでは SSM 接続用のエンドポイントの分(取れなければ null)
export function networkHourly(plan: RegionPlan, networkMode: NetworkMode) {
  return networkMode === "private" ? plan.prices.private_network_hourly_usd : 0;
}

export function hourlyTotals(plan: RegionPlan, option: StorageOption, networkMode: NetworkMode = "public") {
  const network = networkHourly(plan, networkMode);
  return {
    spot: hourlyTotal(plan.prices.spot_min?.hourly_usd, option, network),
    onDemand: hourlyTotal(plan.prices.on_demand_hourly_usd, option, network),
  };
}

// 秒を「約 N 分」に丸める(目安なので分単位)
export function minutes(sec: number) {
  return Math.max(1, Math.round(sec / 60));
}
