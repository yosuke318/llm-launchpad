import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { listLaunchHistory, type LaunchHistoryEntry } from "../api";
import { useRun } from "../runContext";

interface Props {
  onChoose: (entry: LaunchHistoryEntry) => void;
}

// 前に起動した設定の一覧。選ぶと、同じ設定を入れた起動の確認を開く(料金はそのときの値で出し直す)
export default function LaunchHistory({ onChoose }: Props) {
  const { t, i18n } = useTranslation();
  const { view } = useRun();
  const [launches, setLaunches] = useState<LaunchHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  // 起動を始めたら履歴が増えるので、読み直す
  const runId = view.run?.id;
  useEffect(() => {
    let cancelled = false;
    listLaunchHistory()
      .then((r) => {
        if (cancelled) return;
        setLaunches(r.launches);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (error) return <p className="error small">{t("history.loadFailed", { error })}</p>;
  if (!launches.length) return null;

  const date = new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" });

  return (
    <details className="card launch-history">
      <summary>{t("history.open", { count: launches.length })}</summary>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("history.columns.launchedAt")}</th>
              <th>{t("history.columns.model")}</th>
              <th>{t("history.columns.where")}</th>
              <th>{t("history.columns.purchase")}</th>
              <th>{t("history.columns.autoStop")}</th>
              <th>{t("history.columns.envId")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {launches.map((h) => {
              const v = h.variables;
              return (
                <tr key={h.id}>
                  <td className="small">{date.format(new Date(h.launched_at))}</td>
                  <td>
                    {h.model_name ?? <span className="error small">{t("history.unknownModel")}</span>}
                    <div className="muted small">
                      <code>{v.model}</code> · {t("history.context", { length: v.context_length.toLocaleString(i18n.language), kv: v.kv_cache_type })}
                    </div>
                  </td>
                  <td className="small">
                    {v.region}
                    {v.availability_zone && ` (${v.availability_zone})`}
                    <div className="muted">
                      {t(`region.storageNames.${v.model_storage}`)} · {t(`region.networkNames.${v.network_mode}`)}
                    </div>
                  </td>
                  <td className="small">{t(v.purchase_option === "spot" ? "history.spot" : "history.onDemand")}</td>
                  <td className="small">{t("history.autoStop", { idle: v.idle_stop_minutes, hours: v.max_lifetime_hours })}</td>
                  <td className="small">
                    <code>{h.workspace}</code>
                    {h.in_use && <div className="muted">{t("history.inUse")}</div>}
                  </td>
                  <td>
                    <button onClick={() => onChoose(h)} disabled={!h.model_name}>
                      {t("history.choose")}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">{t("history.note")}</p>
    </details>
  );
}
