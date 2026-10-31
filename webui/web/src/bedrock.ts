import type { BedrockEnvironment } from "./api";

export type BedrockEnvState = "creating" | "ready" | "failed" | "destroying" | "destroyFailed" | "unknown";

// 環境の状態。サーバーを起動し直すと実行の記録(run)は消えるので、そのときは Lambda があれば使えるとみなす
export function bedrockEnvState(env: BedrockEnvironment): BedrockEnvState {
  const run = env.run;
  if (run?.command === "destroy") return run.status === "running" ? "destroying" : run.status === "failed" ? "destroyFailed" : "unknown";
  if (run?.command === "launch" && run.status === "running") return "creating";
  if (run?.command === "launch" && run.status === "failed") return "failed";
  return env.function_name ? "ready" : "unknown";
}

// 作っている途中か消している途中なら、一覧を読み直して待つ
export function bedrockEnvBusy(envs: BedrockEnvironment[]) {
  return envs.some((e) => ["creating", "destroying"].includes(bedrockEnvState(e)));
}

// 環境IDの初期値
export function bedrockDefaultEnvId(modelId: string) {
  // jp.anthropic.claude-haiku-4-5-20251001-v1:0 → br-claude-haiku-4-5
  const name = modelId.split(".").slice(-1)[0].split(":")[0].replace(/-\d{8}-v\d+$/, "").replace(/-v\d+$/, "");
  return `br-${name}`.toLowerCase().replace(/[^a-z0-9-]+/g, "-").slice(0, 20).replace(/-+$/, "");
}
