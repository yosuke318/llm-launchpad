import { describe, expect, it } from "vitest";

import type { ComparisonConfig, Preflight, RegionPlan, StorageOption } from "./api";
import {
  BENCH_BASE_SEC,
  BENCH_PER_RUN_SEC,
  DESTROY_SEC,
  bestValue,
  comparisonEstimate,
  configDurationSec,
  configHourly,
  configProblems,
  defaultConfigs,
  nextConfig,
} from "./compare";

function option(id: StorageOption["id"], extra: Partial<StorageOption> = {}): StorageOption {
  return {
    id,
    storage_monthly_usd: 0,
    root_volume_gib: 100,
    running_disk_hourly_usd: 0.01,
    startup_first_sec: 600,
    startup_cached_sec: 300,
    ready: true,
    prerequisite: null,
    ...extra,
  };
}

function plan(extra: Partial<RegionPlan> = {}): RegionPlan {
  return {
    region: "ap-northeast-1",
    model: "qwen3:8b",
    instance_type: "g6.xlarge",
    availability: { available: true, availability_zones: ["ap-northeast-1a"], launchable_zones: ["ap-northeast-1a"] },
    prices: {
      on_demand_hourly_usd: 1.0,
      spot_hourly_usd_by_az: { "ap-northeast-1a": 0.4 },
      spot_min: { availability_zone: "ap-northeast-1a", hourly_usd: 0.4 },
      private_network_hourly_usd: 0.03,
    },
    spot_placement_scores: null,
    storage_options: [option("s3_cache"), option("ebs", { ready: false, prerequisite: "volume" }), option("download")],
    model_store: null,
    measured: { source: "test" },
    warnings: [],
    ...extra,
  };
}

const A: ComparisonConfig = { instance_type: "g4dn.xlarge", model_storage: "s3_cache", purchase_option: "spot" };
const B: ComparisonConfig = { instance_type: "g6.xlarge", model_storage: "s3_cache", purchase_option: "on-demand" };

describe("defaultConfigs / nextConfig", () => {
  it("収まる型の小さいほうから2つを、スポットの S3 キャッシュで並べる", () => {
    expect(defaultConfigs(["g4dn.xlarge", "g6.xlarge", "g5.xlarge"])).toEqual([
      A,
      { instance_type: "g6.xlarge", model_storage: "s3_cache", purchase_option: "spot" },
    ]);
  });

  it("まだない組み合わせを足す。閉じたネットワークでは毎回ダウンロードを選ばない", () => {
    const fitting = ["g4dn.xlarge"];
    expect(nextConfig([A], fitting, "public")).toEqual({ ...A, model_storage: "ebs" });
    const all: ComparisonConfig[] = [A, { ...A, model_storage: "ebs" }];
    expect(nextConfig(all, fitting, "private")).toEqual({ ...A, purchase_option: "on-demand" });
    expect(nextConfig(all, fitting, "public")).toEqual({ ...A, model_storage: "download" });
  });
});

describe("configProblems", () => {
  it("同じ構成、準備のない置き場所、事前チェックに通らない購入方法を見つける", () => {
    const preflight = { can_proceed: { spot: true, on_demand: false } } as Preflight;
    const problems = configProblems(
      [A, A, { ...B, model_storage: "ebs" }, B],
      ["g4dn.xlarge", "g6.xlarge"],
      "public",
      { "g4dn.xlarge": plan(), "g6.xlarge": plan() },
      { "g6.xlarge": preflight },
    );
    expect(problems).toEqual([["duplicate"], ["duplicate"], ["storageNotReady", "checkFailed"], ["checkFailed"]]);
  });

  it("収まらない型、閉じたネットワークの毎回ダウンロード、使えないリージョン", () => {
    const problems = configProblems(
      [{ ...A, instance_type: "g9.tiny" }, { ...A, model_storage: "download" }],
      ["g4dn.xlarge"],
      "private",
      { "g4dn.xlarge": plan({ availability: { available: false, availability_zones: [], launchable_zones: [] } }) },
      {},
    );
    expect(problems).toEqual([["notFit"], ["downloadPrivate", "notOffered"]]);
  });

  it("読み込む前の plan や事前チェックは見ない", () => {
    expect(configProblems([A, B], ["g4dn.xlarge", "g6.xlarge"], "public", {}, {})).toEqual([[], []]);
  });
});

describe("時間と料金の目安", () => {
  it("1時間あたりは、購入方法の単価 + ディスク + 保管 + ネットワーク", () => {
    expect(configHourly(A, plan(), "public")).toBeCloseTo(0.41);
    expect(configHourly(B, plan(), "private")).toBeCloseTo(1.04);
    expect(configHourly(A, undefined, "public")).toBeNull();
  });

  it("起動 + 計測 + 削除の時間を足し、料金は時間 × 1時間あたり", () => {
    const sec = 600 + BENCH_BASE_SEC + BENCH_PER_RUN_SEC * 3 + DESTROY_SEC;
    expect(configDurationSec(A, plan(), 3)).toBe(sec);
    const est = comparisonEstimate([A, B], { "g4dn.xlarge": plan(), "g6.xlarge": plan() }, "public", 3);
    expect(est.totalSec).toBe(sec * 2);
    expect(est.totalUsd).toBeCloseTo(((0.41 + 1.01) * sec) / 3600);
  });

  it("読み込んでいない構成があれば、合計は出さない", () => {
    expect(comparisonEstimate([A, B], { "g4dn.xlarge": plan() }, "public", 3)).toEqual({ totalSec: null, totalUsd: null });
  });
});

describe("bestValue", () => {
  it("小さいほうか大きいほうを選び、値のないものは除く", () => {
    expect(bestValue([0.3, null, 0.2], "lower")).toBe(0.2);
    expect(bestValue([40, undefined, 55], "higher")).toBe(55);
    expect(bestValue([null], "lower")).toBeNull();
  });
});
