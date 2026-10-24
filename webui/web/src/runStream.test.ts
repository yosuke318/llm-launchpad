import { describe, expect, it } from "vitest";

import type { RunSummary } from "./api";
import en from "./locales/en.json";
import ja from "./locales/ja.json";
import { initialRunView, runReducer } from "./runStream";

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
