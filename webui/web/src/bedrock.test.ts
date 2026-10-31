import { describe, expect, it } from "vitest";

import type { BedrockEnvironment, RunSummary } from "./api";
import { bedrockDefaultEnvId, bedrockEnvBusy, bedrockEnvState } from "./bedrock";

const run = (command: RunSummary["command"], status: RunSummary["status"]): RunSummary => ({
  id: "r1", command, workspace: "br", args: [], steps: [], status, exit_code: null, started_at: "", finished_at: null, line_count: 0,
});

const env = (r: RunSummary | null, functionName: string | null = null): BedrockEnvironment => ({
  workspace: "br", model: "m", model_name: "M", region: "ap-northeast-1", run: r, function_name: functionName, availability_zone: null, vpc_endpoint_ids: null,
});

describe("bedrockEnvState", () => {
  it("作っている途中、失敗、使える", () => {
    expect(bedrockEnvState(env(run("launch", "running")))).toBe("creating");
    expect(bedrockEnvState(env(run("launch", "failed")))).toBe("failed");
    expect(bedrockEnvState(env(run("launch", "succeeded"), "fn"))).toBe("ready");
  });

  it("サーバーを起動し直して実行の記録がなくても、Lambda があれば使える", () => {
    expect(bedrockEnvState(env(null, "fn"))).toBe("ready");
    expect(bedrockEnvState(env(null))).toBe("unknown");
  });

  it("削除の途中と失敗", () => {
    expect(bedrockEnvState(env(run("destroy", "running"), "fn"))).toBe("destroying");
    expect(bedrockEnvState(env(run("destroy", "failed"), "fn"))).toBe("destroyFailed");
  });

  it("途中のものがあれば読み直す", () => {
    expect(bedrockEnvBusy([env(run("launch", "succeeded"), "fn"), env(run("destroy", "running"))])).toBe(true);
    expect(bedrockEnvBusy([env(run("launch", "succeeded"), "fn")])).toBe(false);
  });
});

describe("bedrockDefaultEnvId", () => {
  it("推論プロファイルIDから、日付と版を除いた名前にする", () => {
    expect(bedrockDefaultEnvId("jp.anthropic.claude-haiku-4-5-20251001-v1:0")).toBe("br-claude-haiku-4-5");
    expect(bedrockDefaultEnvId("jp.anthropic.claude-sonnet-4-6")).toBe("br-claude-sonnet-4-6");
  });
});
