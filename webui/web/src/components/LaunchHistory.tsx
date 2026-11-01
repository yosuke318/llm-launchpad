import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { deletePreset, listLaunchHistory, listPresets, savePreset, type LaunchHistoryEntry, type LaunchPreset, type LaunchRecord } from "../api";
import { useRun } from "../runContext";

interface Props {
  onChoose: (entry: LaunchRecord) => void;
}

// 名前を付けて保存した設定と、前に起動した設定の一覧。選ぶと、同じ設定を入れた起動の確認を開く(料金はそのときの値で出し直す)
export default function LaunchHistory({ onChoose }: Props) {
  const { t, i18n } = useTranslation();
  const { view } = useRun();
  const [launches, setLaunches] = useState<LaunchHistoryEntry[]>([]);
  const [presets, setPresets] = useState<LaunchPreset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [presetError, setPresetError] = useState<string | null>(null);
  // 名前を入れている履歴の id
  const [saving, setSaving] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

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
      .catch((e: Error) => !cancelled && setError(t("history.loadFailed", { error: e.message })));
    listPresets()
      .then((r) => !cancelled && setPresets(r.presets))
      .catch((e: Error) => !cancelled && setPresetError(t("presets.loadFailed", { error: e.message })));
    return () => {
      cancelled = true;
    };
  }, [runId]);

  function startSaving(h: LaunchHistoryEntry) {
    setSaving(h.id);
    setName(h.model_name ?? h.variables.model);
  }

  async function submit(e: FormEvent, historyId: string) {
    e.preventDefault();
    setBusy(true);
    try {
      const saved = await savePreset(name, historyId);
      setPresets((ps) => [...ps.filter((p) => p.name !== saved.name), saved].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)));
      setSaving(null);
      setPresetError(null);
    } catch (err) {
      setPresetError(t("presets.saveFailed", { error: (err as Error).message }));
    } finally {
      setBusy(false);
    }
  }

  async function remove(p: LaunchPreset) {
    if (!window.confirm(t("presets.confirmDelete", { name: p.name }))) return;
    try {
      await deletePreset(p.id);
      setPresets((ps) => ps.filter((x) => x.id !== p.id));
      setPresetError(null);
    } catch (err) {
      setPresetError(t("presets.deleteFailed", { error: (err as Error).message }));
    }
  }

  if (!launches.length && !presets.length) return error || presetError ? <p className="error small">{error ?? presetError}</p> : null;

  const date = new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" });

  // モデルから環境IDまでの列。保存した設定と履歴で同じものを出す
  const settingsHeaders = (
    <>
      <th>{t("history.columns.model")}</th>
      <th>{t("history.columns.where")}</th>
      <th>{t("history.columns.purchase")}</th>
      <th>{t("history.columns.autoStop")}</th>
      <th>{t("history.columns.envId")}</th>
    </>
  );

  function settingsCells(r: LaunchRecord) {
    const v = r.variables;
    return (
      <>
        <td>
          {r.model_name ?? <span className="error small">{t("history.unknownModel")}</span>}
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
          <code>{r.workspace}</code>
          {r.in_use && <div className="muted">{t("history.inUse")}</div>}
        </td>
      </>
    );
  }

  return (
    <>
      {(presets.length > 0 || presetError) && (
        <section className="card launch-history">
          <h3>{t("presets.title")}</h3>
          {presetError && <p className="error small">{presetError}</p>}
          {presets.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("presets.name")}</th>
                    {settingsHeaders}
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {presets.map((p) => (
                    <tr key={p.id}>
                      <td>
                        <strong>{p.name}</strong>
                        <div className="muted small">{t("presets.savedAt")}: {date.format(new Date(p.saved_at))}</div>
                      </td>
                      {settingsCells(p)}
                      <td>
                        <div className="history-actions">
                          <button onClick={() => onChoose(p)} disabled={!p.model_name}>
                            {t("history.choose")}
                          </button>
                          <button className="secondary" onClick={() => remove(p)}>
                            {t("presets.delete")}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
      {error && <p className="error small">{error}</p>}
      {launches.length > 0 && (
        <details className="card launch-history">
          <summary>{t("history.open", { count: launches.length })}</summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("history.columns.launchedAt")}</th>
                  {settingsHeaders}
                  <th />
                </tr>
              </thead>
              <tbody>
                {launches.map((h) => (
                  <tr key={h.id}>
                    <td className="small">{date.format(new Date(h.launched_at))}</td>
                    {settingsCells(h)}
                    <td>
                      {saving === h.id ? (
                        <form className="history-actions" onSubmit={(e) => submit(e, h.id)}>
                          <input
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                            placeholder={t("presets.namePlaceholder")}
                            aria-label={t("presets.name")}
                            maxLength={40}
                            autoFocus
                          />
                          <button type="submit" disabled={busy || !name.trim()}>
                            {t("presets.submit")}
                          </button>
                          <button type="button" className="secondary" onClick={() => setSaving(null)}>
                            {t("presets.cancel")}
                          </button>
                        </form>
                      ) : (
                        <div className="history-actions">
                          <button onClick={() => onChoose(h)} disabled={!h.model_name}>
                            {t("history.choose")}
                          </button>
                          <button className="secondary" onClick={() => startSaving(h)}>
                            {t("presets.save")}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">{t("history.note")}</p>
          <p className="muted small">{t("presets.note")}</p>
        </details>
      )}
    </>
  );
}
