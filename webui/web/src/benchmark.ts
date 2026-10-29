import type { ChatMessage } from "./api";

// ベンチマークとチャットの画面で使う計算

// 計測の終わりを待つ間に読み直す間隔(ミリ秒)。計測は数分かかる
export const BENCHMARK_POLL_MS = 3000;

// プロンプトを何周流すか(サーバーの MAX_RUNS と同じ範囲)
export const MIN_RUNS = 1;
export const MAX_RUNS = 10;

export function isValidRuns(runs: number) {
  return Number.isInteger(runs) && runs >= MIN_RUNS && runs <= MAX_RUNS;
}

// 見積もりとの差。多ければ +、少なければ − を付ける
export function signed(value: number, digits = 2) {
  const s = Math.abs(value).toFixed(digits);
  if (value > 0) return `+${s}`;
  if (value < 0) return `−${s}`;
  return s;
}

// 送る会話。空の入力は送らない
export function nextMessages(history: ChatMessage[], input: string): ChatMessage[] | null {
  const content = input.trim();
  if (!content) return null;
  return [...history, { role: "user", content }];
}
