import { createContext, useContext, type ReactNode } from "react";

import type { RunSummary } from "./api";
import { useRunStream, type RunView } from "./runStream";

// 実行中の terraform の出力。起動の確認から始めた実行も、Terraform タブで見られるよう、画面全体で1つ持つ
interface RunContextValue {
  view: RunView;
  start: (run: RunSummary) => void;
  // Terraform タブに切り替える
  showOutput: () => void;
}

const RunContext = createContext<RunContextValue | null>(null);

export function RunProvider({ children, showOutput }: { children: ReactNode; showOutput: () => void }) {
  const { view, start } = useRunStream();
  return <RunContext.Provider value={{ view, start, showOutput }}>{children}</RunContext.Provider>;
}

export function useRun() {
  const value = useContext(RunContext);
  if (!value) throw new Error("useRun は RunProvider の中で使う");
  return value;
}
