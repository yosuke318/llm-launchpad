import type { RegionPlan, StorageOption } from "./api";

export const HOURS_PER_MONTH = 730;

// 1時間あたりの料金の目安 = インスタンス + 動かしている間のルートボリューム + 保管の料金(月額 ÷ 730)
export function hourlyTotal(instanceHourly: number | null | undefined, option: StorageOption): number | null {
  if (instanceHourly == null || option.running_disk_hourly_usd == null || option.storage_monthly_usd == null) return null;
  return instanceHourly + option.running_disk_hourly_usd + option.storage_monthly_usd / HOURS_PER_MONTH;
}

export function hourlyTotals(plan: RegionPlan, option: StorageOption) {
  return {
    spot: hourlyTotal(plan.prices.spot_min?.hourly_usd, option),
    onDemand: hourlyTotal(plan.prices.on_demand_hourly_usd, option),
  };
}

// 秒を「約 N 分」に丸める(目安なので分単位)
export function minutes(sec: number) {
  return Math.max(1, Math.round(sec / 60));
}
