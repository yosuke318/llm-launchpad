import type { Progress } from "./api";

// 起動の進み具合の画面で使う計算

// 読み直す間隔(ミリ秒)。インスタンスは取得中でも10秒ごとにしか状態を書かない
export const POLL_MS = 5000;

export function isFinished(p: Progress | null) {
  return p?.state === "ready" || p?.state === "failed";
}

// 秒を「1時間2分」「3分4秒」「5秒」の形にする。単位の文言は呼ぶ側で渡す
export function splitDuration(sec: number) {
  const s = Math.max(0, Math.round(sec));
  return { hours: Math.floor(s / 3600), minutes: Math.floor((s % 3600) / 60), seconds: s % 60 };
}

// サーバーで読んだときから sec 秒たったときの、経過時間と残り時間
export function tick(p: Progress, sinceFetchSec: number) {
  const running = !isFinished(p);
  const elapsed = p.elapsed_sec == null ? null : p.elapsed_sec + (running ? sinceFetchSec : 0);
  const remaining = p.remaining_sec == null || !running ? null : Math.max(p.remaining_sec - sinceFetchSec, 0);
  return { elapsed, remaining, overdue: remaining === 0 };
}

// 段階にかかった時間(秒)。進行中なら now まで
export function stageSec(startedAt: string | null, finishedAt: string | null, now: number) {
  if (!startedAt) return null;
  const end = finishedAt ? Date.parse(finishedAt) : now;
  return Math.max(0, (end - Date.parse(startedAt)) / 1000);
}

// OpenAI の Python SDK で送る例。base_url は /v1 まで、api_key は任意の文字列でよい
export function openaiPythonExample(endpoint: string, model: string) {
  return [
    "from openai import OpenAI",
    "",
    `client = OpenAI(base_url="${endpoint}/v1", api_key="ollama")`,
    "res = client.chat.completions.create(",
    `    model="${model}",`,
    '    messages=[{"role": "user", "content": "こんにちは"}],',
    ")",
    "print(res.choices[0].message.content)",
  ].join("\n");
}

// 起動処理のログを見るコマンド(SSM でシェルに入ってから実行する)
export function tailLogCommand(logPath: string) {
  return `sudo tail -n 100 ${logPath}`;
}
