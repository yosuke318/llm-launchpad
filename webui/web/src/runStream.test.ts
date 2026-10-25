import { describe, expect, it } from "vitest";

import type { RunSummary } from "./api";
import en from "./locales/en.json";
import ja from "./locales/ja.json";
import { initialRunView, runReducer } from "./runStream";
import { isTight, vramSegments } from "./vram";

const run: RunSummary = {
  id: "abc",
  command: "plan",
  workspace: "default",
  args: ["plan", "-input=false", "-no-color"],
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
