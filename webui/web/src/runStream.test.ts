import { describe, expect, it } from "vitest";

import type { ModelEstimate, Progress, RegionPlan, RunSummary } from "./api";
import en from "./locales/en.json";
import ja from "./locales/ja.json";
import { isFinished, openaiPythonExample, splitDuration, stageSec, tick } from "./progress";
import { initialRunView, runReducer } from "./runStream";
import { isTight, vramSegments } from "./vram";
import { hourlyTotal, minutes } from "./cost";
import {
  bestPlacement,
  defaultEnvId,
  ebsVolumeZone,
  isValidEnvId,
  launchHourly,
  launchVariables,
  storageAllowed,
  placementRisk,
  validIdleStop,
  validMaxLifetime,
  zonePlacement,
} from "./launch";

const run: RunSummary = {
  id: "abc",
  command: "plan",
  workspace: "default",
  args: ["plan", "-input=false", "-no-color"],
  steps: [["plan", "-input=false", "-no-color"]],
  status: "running",
  exit_code: null,
  started_at: "2026-10-24T00:00:00+00:00",
  finished_at: null,
  line_count: 0,
};

describe("runReducer", () => {
  it("adds log lines and finishes with the exit code", () => {
    let s = runReducer(initialRunView, { type: "start", run });
    s = runReducer(s, { type: "log", index: 0, line: "a" });
    s = runReducer(s, { type: "log", index: 1, line: "b" });
    s = runReducer(s, { type: "end", status: "failed", exitCode: 1 });
    expect(s.lines).toEqual(["a", "b"]);
    expect(s.status).toBe("failed");
    expect(s.exitCode).toBe(1);
  });

  it("ignores lines it already has after reconnecting", () => {
    let s = runReducer(initialRunView, { type: "start", run });
    s = runReducer(s, { type: "log", index: 0, line: "a" });
    s = runReducer(s, { type: "disconnected" });
    expect(s.reconnecting).toBe(true);
    s = runReducer(s, { type: "log", index: 0, line: "a" });
    s = runReducer(s, { type: "log", index: 1, line: "b" });
    expect(s.lines).toEqual(["a", "b"]);
    expect(s.reconnecting).toBe(false);
  });

  it("does not show reconnecting after the run ended", () => {
    let s = runReducer(initialRunView, { type: "start", run });
    s = runReducer(s, { type: "end", status: "succeeded", exitCode: 0 });
    expect(runReducer(s, { type: "disconnected" }).reconnecting).toBe(false);
  });

  it("starts a new run with an empty log", () => {
    let s = runReducer(initialRunView, { type: "start", run });
    s = runReducer(s, { type: "log", index: 0, line: "a" });
    s = runReducer(s, { type: "start", run: { ...run, id: "def" } });
    expect(s.lines).toEqual([]);
    expect(s.run?.id).toBe("def");
  });
});

describe("locales", () => {
  // 日本語と英語で、同じキーがそろっていること
  function keys(obj: object, prefix = ""): string[] {
    return Object.entries(obj).flatMap(([k, v]) =>
      typeof v === "object" && v !== null ? keys(v, `${prefix}${k}.`) : [`${prefix}${k}`],
    );
  }

  it("ja and en have the same keys", () => {
    expect(keys(en).sort()).toEqual(keys(ja).sort());
  });
});

describe("vramSegments", () => {
  const base = {
    weights_gib: 4.84,
    kv_cache_gib: 1.13,
    overhead_gib: 1.5,
    required_gib: 7.47,
    instance_vram_gib: 15,
    headroom_gib: 7.53,
    context_length: 8192,
    kv_cache_type: "f16",
  };

  it("splits the instance VRAM into weights, KV cache, overhead and free", () => {
    const s = vramSegments(base);
    expect(s.weights + s.kvCache + s.overhead + s.free).toBeCloseTo(100);
    expect(s.weights).toBeCloseTo((4.84 / 15) * 100);
    expect(s.overflow).toBe(false);
  });

  it("marks overflow and fills the bar when nothing fits", () => {
    const s = vramSegments({ ...base, instance_vram_gib: 0, headroom_gib: -7.47 });
    expect(s.overflow).toBe(true);
    expect(s.weights + s.kvCache + s.overhead).toBeCloseTo(100);
  });

  it("flags tight headroom under 1 GiB", () => {
    expect(isTight({ ...base, headroom_gib: 0.37 })).toBe(true);
    expect(isTight(base)).toBe(false);
  });
});

describe("hourlyTotal", () => {
  const option = {
    id: "s3_cache" as const,
    storage_monthly_usd: 0.13,
    root_volume_gib: 96,
    running_disk_hourly_usd: 0.0784,
    startup_first_sec: 563,
    startup_cached_sec: 545,
    ready: true,
    prerequisite: null,
  };

  it("adds the instance, the root volume and the storage spread over the month", () => {
    expect(hourlyTotal(0.3407, option)).toBeCloseTo(0.3407 + 0.0784 + 0.13 / 730);
  });

  it("is unknown when a price is missing", () => {
    expect(hourlyTotal(null, option)).toBeNull();
    expect(hourlyTotal(0.34, { ...option, storage_monthly_usd: null })).toBeNull();
  });

  it("rounds startup time to minutes, at least 1", () => {
    expect(minutes(563)).toBe(9);
    expect(minutes(10)).toBe(1);
  });
});

describe("launch helpers", () => {
  it("makes an environment ID from a model tag", () => {
    expect(defaultEnvId("qwen3:8b")).toBe("qwen3-8b");
    expect(defaultEnvId("nemotron-3-super:120b")).toBe("nemotron-3-super-120");
    expect(defaultEnvId("hf.co/user/Model:Q4_K_M")).toBe("hf-co-user-model-q4");
    expect(isValidEnvId(defaultEnvId("mistral-small3.2:24b"))).toBe(true);
  });

  it("rejects default and invalid environment IDs", () => {
    expect(isValidEnvId("default")).toBe(false);
    expect(isValidEnvId("Bad_Name")).toBe(false);
    expect(isValidEnvId("a".repeat(21))).toBe(false);
  });

  it("turns placement scores into risk", () => {
    expect([placementRisk(9), placementRisk(5), placementRisk(1)]).toEqual(["low", "medium", "high"]);
    expect(bestPlacement({ "ap-northeast-1a": 3, "ap-northeast-1c": 7 })).toEqual({ availabilityZone: "ap-northeast-1c", score: 7, risk: "low" });
    expect(bestPlacement(null)).toBeNull();
  });

  it("checks auto stop values like terraform does", () => {
    expect([validIdleStop(0), validIdleStop(10), validIdleStop(5), validIdleStop(1441)]).toEqual([true, true, false, false]);
    expect([validMaxLifetime(0), validMaxLifetime(720), validMaxLifetime(721), validMaxLifetime(1.5)]).toEqual([true, true, false, false]);
  });

  // 起動の確認で見せる値と、tfvars に書く値。テストで使うところだけ埋める
  const option = {
    id: "ebs" as const,
    storage_monthly_usd: 10.5,
    root_volume_gib: 96,
    running_disk_hourly_usd: 0.0784,
    startup_first_sec: 700,
    startup_cached_sec: 600,
    ready: true,
    prerequisite: null,
  };
  const estimate = {
    model: { tag: "qwen3:8b" },
    // コンテキスト長をモデルの最大で抑えたときも、見積もりに使った値を書く
    vram_estimate: { context_length: 40960, kv_cache_type: "q8_0" },
    context_capped: true,
  } as unknown as ModelEstimate;
  const plan = {
    region: "us-east-1",
    instance_type: "g4dn.xlarge",
    prices: {
      on_demand_hourly_usd: 0.526,
      spot_hourly_usd_by_az: { "us-east-1a": 0.25, "us-east-1b": 0.21 },
      spot_min: { availability_zone: "us-east-1b", hourly_usd: 0.21 },
      private_network_hourly_usd: 0.042,
    },
    model_store: { volumes: [{ name: "other", id: "vol-1", availability_zone: "us-east-1c" }, { name: "default", id: "vol-2", availability_zone: "us-east-1a" }] },
  } as unknown as RegionPlan;

  it("writes the shown values to tfvars", () => {
    const settings = { purchaseOption: "on-demand" as const, idleStopMinutes: 0, maxLifetimeHours: 24, availabilityZone: null };
    expect(launchVariables(estimate, plan, option, settings)).toEqual({
      model: "qwen3:8b",
      region: "us-east-1",
      context_length: 40960,
      kv_cache_type: "q8_0",
      model_storage: "ebs",
      network_mode: "public",
      purchase_option: "on-demand",
      idle_stop_minutes: 0,
      max_lifetime_hours: 24,
    });
  });

  it("writes the chosen zone except for EBS persistence", () => {
    const settings = { purchaseOption: "spot" as const, idleStopMinutes: 30, maxLifetimeHours: 8, availabilityZone: "us-east-1a" };
    expect(launchVariables(estimate, plan, { ...option, id: "s3_cache" }, settings).availability_zone).toBe("us-east-1a");
    expect(launchVariables(estimate, plan, option, settings)).not.toHaveProperty("availability_zone");
    expect(ebsVolumeZone(plan)).toBe("us-east-1a");
    expect(ebsVolumeZone({ ...plan, model_store: null })).toBeNull();
  });

  it("shows the placement score of the chosen zone", () => {
    const scores = { "us-east-1a": 3, "us-east-1b": 8 };
    expect(zonePlacement(scores, "us-east-1a")).toEqual({ availabilityZone: "us-east-1a", score: 3, risk: "high" });
    expect(zonePlacement(scores, null)).toEqual({ availabilityZone: "us-east-1b", score: 8, risk: "low" });
    expect(zonePlacement(scores, "us-east-1c")?.availabilityZone).toBe("us-east-1b");
  });

  it("totals the hourly price for the chosen purchase option", () => {
    const disk = 0.0784 + 10.5 / 730;
    expect(launchHourly(plan, option, "spot")).toBeCloseTo(0.21 + disk);
    expect(launchHourly(plan, option, "on-demand")).toBeCloseTo(0.526 + disk);
    expect(launchHourly(plan, option, "spot", "us-east-1a")).toBeCloseTo(0.25 + disk);
    expect(launchHourly(plan, option, "spot", "us-east-1c")).toBeNull();
    const noSpot = { ...plan, prices: { ...plan.prices, spot_min: null } };
    expect(launchHourly(noSpot, option, "spot")).toBeNull();
  });

  it("adds the VPC endpoints for the private network", () => {
    const disk = 0.0784 + 10.5 / 730;
    expect(launchHourly(plan, option, "spot", null, "private")).toBeCloseTo(0.21 + disk + 0.042);
    const unknown = { ...plan, prices: { ...plan.prices, private_network_hourly_usd: null } };
    expect(launchHourly(unknown, option, "spot", null, "private")).toBeNull();
    expect(launchHourly(unknown, option, "spot")).toBeCloseTo(0.21 + disk);
    const settings = { purchaseOption: "spot" as const, idleStopMinutes: 30, maxLifetimeHours: 8, availabilityZone: null };
    expect(launchVariables(estimate, plan, option, settings, "private").network_mode).toBe("private");
  });

  it("does not allow download in the private network", () => {
    expect(storageAllowed("download", "private")).toBe(false);
    expect(storageAllowed("s3_cache", "private")).toBe(true);
    expect(storageAllowed("ebs", "private")).toBe(true);
    expect(storageAllowed("download", "public")).toBe(true);
  });
});

describe("progress helpers", () => {
  const base = { state: "running", elapsed_sec: 100, remaining_sec: 30 } as Progress;

  it("splits durations", () => {
    expect(splitDuration(3725)).toEqual({ hours: 1, minutes: 2, seconds: 5 });
    expect(splitDuration(-3)).toEqual({ hours: 0, minutes: 0, seconds: 0 });
  });

  it("moves the clock forward only while launching", () => {
    expect(tick(base, 10)).toEqual({ elapsed: 110, remaining: 20, overdue: false });
    expect(tick(base, 45)).toEqual({ elapsed: 145, remaining: 0, overdue: true });
    expect(tick({ ...base, state: "ready", remaining_sec: null }, 10)).toEqual({ elapsed: 100, remaining: null, overdue: false });
    expect([isFinished(base), isFinished({ ...base, state: "failed" }), isFinished(null)]).toEqual([false, true, false]);
  });

  it("measures a stage until now while it is active", () => {
    const now = Date.parse("2026-10-27T03:00:00Z");
    expect(stageSec("2026-10-27T02:59:00Z", null, now)).toBe(60);
    expect(stageSec("2026-10-27T02:58:00Z", "2026-10-27T02:58:20Z", now)).toBe(20);
    expect(stageSec(null, null, now)).toBeNull();
  });

  it("writes the OpenAI SDK example with /v1", () => {
    const example = openaiPythonExample("http://localhost:11434", "qwen3:8b", "Hello");
    expect(example).toContain('base_url="http://localhost:11434/v1"');
    expect(example).toContain('"content": "Hello"');
  });
});
