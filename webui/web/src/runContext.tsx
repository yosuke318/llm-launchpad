import { createContext, useContext, type ReactNode } from "react";

import type { RunSummary } from "./api";
import { useRunStream, type RunView } from "./runStream";

// 実行中の terraform の出力。起動の確認から始めた実行も、Terraform タブで見られるよう、画面全体で1つ持つ
interface RunContextValue {
  view: RunView;
  start: (run: RunSummary) => void;
  // 起動の進み具合のタブに切り替える(起動の確認で Yes を押したあと)
  showProgress: () => void;
  // Terraform タブに切り替える(全部の出力を見るとき)
  showTerraform: () => void;
}

const RunContext = createContext<RunContextValue | null>(null);

export function RunProvider({
  children,
  showProgress,
  showTerraform,
}: {
  children: ReactNode;
  showProgress: () => void;
  showTerraform: () => void;
}) {
  const { view, start } = useRunStream();
  return <RunContext.Provider value={{ view, start, showProgress, showTerraform }}>{children}</RunContext.Provider>;
}

export function useRun() {
  const value = useContext(RunContext);
  if (!value) throw new Error("useRun は RunProvider の中で使う");
  return value;
}
