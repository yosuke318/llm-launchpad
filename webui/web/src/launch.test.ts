import { describe, expect, it } from "vitest";

import type { LaunchHistoryEntry } from "./api";
import { presetContextLength, presetFromHistory } from "./launch";

const entry: LaunchHistoryEntry = {
  id: "h1",
  launched_at: "2026-10-30T03:00:00+00:00",
  workspace: "qwen3-8b",
  variables: {
    model: "qwen3:8b",
    region: "eu-north-1",
    context_length: 8192,
    kv_cache_type: "f16",
    model_storage: "s3_cache",
    network_mode: "public",
    purchase_option: "spot",
    idle_stop_minutes: 20,
    max_lifetime_hours: 4,
    availability_zone: "eu-north-1b",
  },
  model_name: "Qwen3 8B",
  in_use: false,
};

describe("presetFromHistory", () => {
  it("履歴の購入方法・自動停止・AZ・環境IDで起動の確認を開く", () => {
    expect(presetFromHistory(entry)).toMatchObject({
      settings: { purchaseOption: "spot", idleStopMinutes: 20, maxLifetimeHours: 4, availabilityZone: "eu-north-1b" },
      envId: "qwen3-8b",
    });
  });

  it("AZを書いていなければ terraform に選ばせる", () => {
    const { availability_zone: _, ...variables } = entry.variables;
    expect(presetFromHistory({ ...entry, variables }).settings.availabilityZone).toBeNull();
  });

  it("同じ環境IDの環境が動いていれば、その環境IDは使わない", () => {
    expect(presetFromHistory({ ...entry, in_use: true }).envId).toBeNull();
  });
});

describe("presetContextLength", () => {
  const choices = [4096, 8192, 16384, 32768];

  it("選択肢にある値はそのまま", () => {
    expect(presetContextLength(choices, 8192)).toBe(8192);
  });

  it("モデルの最大に合わせた値は、それ以上で一番小さい選択肢にする", () => {
    expect(presetContextLength(choices, 10000)).toBe(16384);
  });

  it("どの選択肢より大きければ、一番大きいもの", () => {
    expect(presetContextLength(choices, 65536)).toBe(32768);
  });
});
