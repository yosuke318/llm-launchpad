import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  destroyEnvironment,
  getEnvironmentsOverview,
  startEnvironment,
  stopEnvironment,
  type Environment,
  type EnvironmentsOverview,
  type Leftover,
} from "../api";
import {
  canDestroy,
  canStart,
  canStop,
  environmentStatus,
  isChanging,
  OVERVIEW_BUSY_POLL_MS,
  OVERVIEW_POLL_MS,
  terminateCommand,
  type EnvironmentStatus,
} from "../environments";
import { splitDuration } from "../progress";
import { useRun } from "../runContext";

// 動いている環境の一覧。止める・動かし直す・削除する。どの state にもない消し忘れのリソースも出す
export default function EnvironmentsPanel() {
  const { t, i18n } = useTranslation();
  const { view, start, showTerraform } = useRun();
  const [overview, setOverview] = useState<EnvironmentsOverview | null>(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<Environment | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const timer = useRef<number | undefined>(undefined);
  // 読み直すたびに増やす。前に頼んだ分の結果があとから届いても使わない(読み直しの予約が二重にならないように)
  const generation = useRef(0);

  const load = useCallback(() => {
    window.clearTimeout(timer.current);
    const current = ++generation.current;
    setLoading(true);
    getEnvironmentsOverview()
      .then((o) => {
        if (current !== generation.current) return;
        setOverview(o);
        setFetchedAt(Date.now());
        setError(null);
        timer.current = window.setTimeout(load, o.environments.some(isChanging) ? OVERVIEW_BUSY_POLL_MS : OVERVIEW_POLL_MS);
      })
      .catch((e: Error) => {
        if (current !== generation.current) return;
        setError(t("environments.loadFailed", { error: e.message }));
        timer.current = window.setTimeout(load, OVERVIEW_POLL_MS);
      })
      .finally(() => {
        if (current === generation.current) setLoading(false);
      });
  }, [t]);

  // 起動や削除が始まったときと終わったときにも読み直す
  useEffect(() => {
    load();
    return () => {
      generation.current++;
      window.clearTimeout(timer.current);
    };
  }, [load, view.run?.id, view.status]);

  // 経過時間と料金の目安を1秒ごとに進める
  const anyRunning = overview?.environments.some((e) => e.elapsed_sec != null) ?? false;
  useEffect(() => {
    if (!anyRunning) return;
    const id = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [anyRunning]);
  const sinceFetch = Math.max(0, (clock - fetchedAt) / 1000);

  const usd = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    [i18n.language],
  );
  const duration = (sec: number) => {
    const d = splitDuration(sec);
    if (d.hours) return t("progress.duration.hours", d);
    if (d.minutes) return t("progress.duration.minutes", d);
    return t("progress.duration.seconds", d);
  };

  async function act(env: Environment, action: "stop" | "start") {
    setActing(env.workspace);
    setActionError(null);
    try {
      await (action === "stop" ? stopEnvironment : startEnvironment)(env.workspace);
      load();
    } catch (e) {
      setActionError(t(`environments.${action}Failed`, { workspace: env.workspace, error: (e as Error).message }));
    } finally {
      setActing(null);
    }
  }

  async function destroy(env: Environment) {
    setActionError(null);
    try {
      const run = await destroyEnvironment(env.workspace);
      start(run);
      setConfirming(null);
      load();
    } catch (e) {
      setConfirming(null);
      setActionError(e instanceof ApiError && e.status === 409 ? t("terraform.busy") : t("environments.destroyFailed", { workspace: env.workspace, error: (e as Error).message }));
    }
  }

  const environments = overview?.environments ?? [];

  return (
    <div className="environments-page">
      <section className="card">
        <div className="progress-header">
          <h2>{t("environments.heading")}</h2>
          <button className="secondary" onClick={load} disabled={loading}>
            {loading ? t("environments.loading") : t("environments.reload")}
          </button>
        </div>
        <p className="muted small">{t("environments.lead")}</p>
        {error && <p className="error">{error}</p>}
        {actionError && <p className="error">{actionError}</p>}
        {overview && !environments.length && <p className="muted">{t("environments.empty")}</p>}

        <ul className="environment-list">
          {environments.map((env) => {
            const status = environmentStatus(env);
            const elapsed = env.elapsed_sec == null ? null : env.elapsed_sec + sinceFetch;
            const cost = env.hourly_usd == null || elapsed == null ? null : (env.hourly_usd * elapsed) / 3600;
            return (
              <li key={env.workspace} className="environment">
                <div className="environment-title">
                  <strong>{env.workspace}</strong>
                  <span className={`badge ${badgeClass(status)}`}>{t(`environments.states.${status}`)}</span>
                </div>
                <dl className="launch-summary">
                  <div>
                    <dt>{t("environments.model")}</dt>
                    <dd>{env.model ? <code>{env.model}</code> : t("region.unknown")}</dd>
                  </div>
                  <div>
                    <dt>{t("environments.instance")}</dt>
                    <dd>
                      <code>{env.instance_type ?? "?"}</code>
                      {env.purchase_option && ` · ${t(`progress.purchase.${env.purchase_option}`)}`}
                      {env.instance && (
                        <>
                          {" · "}
                          <code>{env.instance.id}</code>
                        </>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("environments.region")}</dt>
                    <dd>
                      {env.region}
                      {env.instance && ` (${env.instance.availability_zone})`}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("environments.elapsed")}</dt>
                    <dd>{elapsed == null ? "—" : duration(elapsed)}</dd>
                  </div>
                  <div>
                    <dt>{t("environments.cost")}</dt>
                    <dd>
                      {cost == null ? "—" : usd.format(cost)}
                      {env.hourly_usd != null && <span className="muted small"> {t("region.perHour", { price: usd.format(env.hourly_usd) })}</span>}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("environments.resources")}</dt>
                    <dd>{t("environments.resourceCount", { n: env.resource_count })}</dd>
                  </div>
                </dl>
                {env.state_error && <p className="warning small">{t("environments.stateError", { error: env.state_error })}</p>}
                {!env.has_tfvars && env.resource_count > 0 && <p className="muted small">{t("environments.noTfvars", { workspace: env.workspace })}</p>}

                <div className="environment-actions">
                  {canStop(env) && (
                    <button className="secondary" disabled={acting === env.workspace} onClick={() => act(env, "stop")}>
                      {t("environments.stop")}
                    </button>
                  )}
                  {canStart(env) && (
                    <button className="secondary" disabled={acting === env.workspace} onClick={() => act(env, "start")}>
                      {t("environments.start")}
                    </button>
                  )}
                  {canDestroy(env) && (
                    <button className="danger" onClick={() => setConfirming(env)}>
                      {t("environments.destroy")}
                    </button>
                  )}
                  {env.run && (
                    <button className="secondary" onClick={showTerraform}>
                      {t("environments.showTerraform")}
                    </button>
                  )}
                </div>
                {env.run?.command === "destroy" && env.run.status === "failed" && <p className="error small">{t("environments.destroyRunFailed")}</p>}
              </li>
            );
          })}
        </ul>
        <p className="muted small">{t("environments.costNote")}</p>
        {overview?.warnings.map((w) => (
          <p key={w} className="warning small">
            {w}
          </p>
        ))}
      </section>

      {overview && <LeftoversCard overview={overview} />}
      {confirming && <DestroyDialog env={confirming} onYes={() => destroy(confirming)} onClose={() => setConfirming(null)} />}
    </div>
  );
}

function badgeClass(status: EnvironmentStatus) {
  if (status === "running") return "succeeded";
  if (status === "stopped" || status === "noInstance") return "stopped";
  return "running";
}

function LeftoversCard({ overview }: { overview: EnvironmentsOverview }) {
  const { t } = useTranslation();
  const { leftovers, regions, unchecked_regions } = overview;

  return (
    <section className="card">
      <h2>{t("environments.leftoversHeading")}</h2>
      <p className="muted small">{t("environments.leftoversLead", { regions: regions.join(", ") })}</p>
      {unchecked_regions.length > 0 && <p className="warning small">{t("environments.unchecked", { regions: unchecked_regions.join(", ") })}</p>}
      {leftovers.length === 0 ? (
        <p className="small">{t("environments.noLeftovers")}</p>
      ) : (
        <>
          <p className="warning small">{t("environments.leftoversHelp")}</p>
          <ul className="leftover-list">
            {leftovers.map((x) => (
              <LeftoverRow key={x.arn} leftover={x} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function LeftoverRow({ leftover: x }: { leftover: Leftover }) {
  const { t } = useTranslation();
  const command = terminateCommand(x);
  return (
    <li className="leftover">
      <div>
        <code>{x.resource_type}</code> <code>{x.resource_id}</code>
        {x.instance_state && <span className="badge running">{t(`environments.states.${x.instance_state}`)}</span>}
      </div>
      <div className="muted small">
        {[x.region, x.env_id && t("environments.envId", { id: x.env_id }), x.name, x.created_at && t("environments.createdAt", { at: x.created_at })]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {command && <pre className="command">{command}</pre>}
    </li>
  );
}

function DestroyDialog({ env, onYes, onClose }: { env: Environment; onYes: () => Promise<void>; onClose: () => void }) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  async function yes() {
    setSubmitting(true);
    await onYes();
  }

  return (
    <dialog ref={dialogRef} className="launch-dialog" aria-labelledby="destroy-title" onCancel={onClose}>
      <h2 id="destroy-title">{t("environments.destroyTitle", { workspace: env.workspace })}</h2>
      <p className="small">{t("environments.destroyLead", { n: env.resource_count })}</p>
      <ul className="small">
        <li>{t("environments.destroyInstance")}</li>
        <li>{t("environments.destroyKeeps")}</li>
        <li>{t("environments.destroyTfvars", { path: `terraform/tfvars/${env.workspace}.tfvars.json` })}</li>
      </ul>
      <label className="field">
        <span>{t("environments.destroyType", { workspace: env.workspace })}</span>
        <input type="text" value={typed} onChange={(e) => setTyped(e.target.value.trim())} spellCheck={false} autoComplete="off" />
      </label>
      <div className="dialog-buttons">
        <button className="secondary" onClick={onClose}>
          {t("environments.cancel")}
        </button>
        <button className="danger" disabled={typed !== env.workspace || submitting} onClick={yes}>
          {t("environments.destroyYes")}
        </button>
      </div>
    </dialog>
  );
}
