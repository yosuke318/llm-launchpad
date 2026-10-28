import type { Environment, Leftover } from "./api";

// 環境の一覧の画面で使う判定

// 一覧を読み直す間隔(ミリ秒)。止めている途中などは短くする
export const OVERVIEW_POLL_MS = 30000;
export const OVERVIEW_BUSY_POLL_MS = 5000;

export type EnvironmentStatus = "launching" | "destroying" | "pending" | "running" | "stopping" | "stopped" | "shutting-down" | "noInstance";

export function environmentStatus(env: Environment): EnvironmentStatus {
  if (env.run?.status === "running") return env.run.command === "destroy" ? "destroying" : "launching";
  return env.instance?.state ?? "noInstance";
}

// 状態が変わっている途中か(短い間隔で読み直す)
export function isChanging(env: Environment) {
  return ["launching", "destroying", "pending", "stopping", "shutting-down"].includes(environmentStatus(env));
}

export function canStop(env: Environment) {
  return environmentStatus(env) === "running";
}

export function canStart(env: Environment) {
  return environmentStatus(env) === "stopped";
}

export function canDestroy(env: Environment) {
  const s = environmentStatus(env);
  return env.has_tfvars && s !== "launching" && s !== "destroying";
}

// 消し忘れを消すときに使う AWS CLI のコマンド。インスタンスだけ(ほかは種類ごとにコマンドが違うので、コンソールで消す)
export function terminateCommand(leftover: Leftover) {
  if (leftover.resource_type !== "ec2:instance") return null;
  return `aws ec2 terminate-instances --region ${leftover.region} --instance-ids ${leftover.resource_id}`;
}
