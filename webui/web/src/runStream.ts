import { useEffect, useReducer } from "react";

import { runEventsUrl, type RunStatus, type RunSummary } from "./api";

// 実行中のコマンドの出力。SSE の event: log を1行ずつ足し、event: end で終わる

export interface RunView {
  run: RunSummary | null;
  lines: string[];
  status: RunStatus | null;
  exitCode: number | null;
  reconnecting: boolean;
}

export type RunAction =
  | { type: "start"; run: RunSummary }
  | { type: "log"; index: number; line: string }
  | { type: "end"; status: RunStatus; exitCode: number | null }
  | { type: "disconnected" };

export const initialRunView: RunView = { run: null, lines: [], status: null, exitCode: null, reconnecting: false };

export function runReducer(state: RunView, action: RunAction): RunView {
  switch (action.type) {
    case "start":
      return { run: action.run, lines: [], status: "running", exitCode: null, reconnecting: false };
    case "log": {
      // つなぎ直したときに同じ行が届いても、二重に足さない(index はサーバーの行番号)
      if (action.index < state.lines.length) return { ...state, reconnecting: false };
      return { ...state, lines: [...state.lines, action.line], reconnecting: false };
    }
    case "end":
      return { ...state, status: action.status, exitCode: action.exitCode, reconnecting: false };
    case "disconnected":
      return state.status === "running" ? { ...state, reconnecting: true } : state;
  }
}

export function useRunStream() {
  const [view, dispatch] = useReducer(runReducer, initialRunView);
  const runId = view.run?.id;

  useEffect(() => {
    if (!runId) return;
    // EventSource は切れると自動でつなぎ直し、最後の id を Last-Event-ID で送る。サーバーはその続きから送る
    const source = new EventSource(runEventsUrl(runId));
    source.addEventListener("log", (e) => {
      const ev = e as MessageEvent<string>;
      dispatch({ type: "log", index: Number(ev.lastEventId), line: JSON.parse(ev.data).line });
    });
    source.addEventListener("end", (e) => {
      const data = JSON.parse((e as MessageEvent<string>).data);
      dispatch({ type: "end", status: data.status, exitCode: data.exit_code });
      // 閉じないと、終わったあともつなぎ直してしまう
      source.close();
    });
    source.onerror = () => dispatch({ type: "disconnected" });
    return () => source.close();
  }, [runId]);

  return {
    view,
    start: (run: RunSummary) => dispatch({ type: "start", run }),
  };
}
