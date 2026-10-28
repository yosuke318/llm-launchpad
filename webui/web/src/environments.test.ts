import { describe, expect, it } from "vitest";

import type { Environment, EnvironmentInstance, Leftover, RunSummary } from "./api";
import { canDestroy, canStart, canStop, environmentStatus, isChanging, terminateCommand } from "./environments";

const instance: EnvironmentInstance = {
  id: "i-0aaa",
  state: "running",
  instance_type: "g6.xlarge",
  availability_zone: "ap-northeast-1a",
  launch_time: "2026-10-27T03:00:00+00:00",
  purchase_option: "spot",
  env_id: "qwen3-8b",
  region: "ap-northeast-1",
};

const run: RunSummary = {
  id: "r1",
  command: "destroy",
  workspace: "qwen3-8b",
  args: ["destroy"],
  steps: [["init"], ["destroy"]],
  status: "running",
  exit_code: null,
  started_at: "2026-10-27T03:00:00Z",
  finished_at: null,
  line_count: 0,
};

function env(overrides: Partial<Environment> = {}): Environment {
  return {
    workspace: "qwen3-8b",
    region: "ap-northeast-1",
    model: "qwen3:8b",
    instance_type: "g6.xlarge",
    purchase_option: "spot",
    resource_count: 20,
    has_tfvars: true,
    instance,
    elapsed_sec: 60,
    hourly_usd: 0.36,
    cost_usd: 0.006,
    run: null,
    state_error: null,
    ...overrides,
  };
}

describe("環境の一覧", () => {
  it("動いている環境は止められて、削除もできる", () => {
    const e = env();
    expect(environmentStatus(e)).toBe("running");
    expect([canStop(e), canStart(e), canDestroy(e), isChanging(e)]).toEqual([true, false, true, false]);
  });

  it("止めている環境は再開できる", () => {
    const e = env({ instance: { ...instance, state: "stopped" } });
    expect([canStop(e), canStart(e), canDestroy(e)]).toEqual([false, true, true]);
  });

  it("削除の途中は、インスタンスの状態より削除中を出し、ボタンを出さない", () => {
    const e = env({ run });
    expect(environmentStatus(e)).toBe("destroying");
    expect([canStop(e), canDestroy(e), isChanging(e)]).toEqual([false, false, true]);
    expect(environmentStatus(env({ run: { ...run, command: "launch" } }))).toBe("launching");
    expect(environmentStatus(env({ run: { ...run, status: "failed" } }))).toBe("running");
  });

  it("tfvars がなければ削除できない", () => {
    expect(canDestroy(env({ has_tfvars: false }))).toBe(false);
    expect(environmentStatus(env({ instance: null }))).toBe("noInstance");
  });

  it("消し忘れたインスタンスには終了のコマンドを出す", () => {
    const leftover: Leftover = {
      region: "ap-northeast-1",
      arn: "arn:aws:ec2:ap-northeast-1:123456789012:instance/i-0left",
      resource_type: "ec2:instance",
      resource_id: "i-0left",
      env_id: "old",
      name: null,
      created_at: null,
    };
    expect(terminateCommand(leftover)).toBe("aws ec2 terminate-instances --region ap-northeast-1 --instance-ids i-0left");
    expect(terminateCommand({ ...leftover, resource_type: "ec2:vpc" })).toBeNull();
  });
});
