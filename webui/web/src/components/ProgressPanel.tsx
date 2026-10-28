import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, getProgress, listEnvironments, type Progress, type ProgressStage } from "../api";
import { isFinished, openaiPythonExample, POLL_MS, splitDuration, stageSec, tailLogCommand, tick } from "../progress";
import { useRun } from "../runContext";

const ICON = { done: "✓", active: "●", failed: "✕", pending: "○" } as const;

// 起動の進み具合。apply から使える状態になるまでを段階ごとに出し、使えるようになったら接続のしかたを出す
export default function ProgressPanel() {
  const { t } = useTranslation();
  const { view, showTerraform } = useRun();
  const launched = view.run?.command === "launch" ? view.run.workspace : null;

  const [environments, setEnvironments] = useState<string[]>([]);
  const [workspace, setWorkspace] = useState<string | null>(launched);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [fetchedAt, setFetchedAt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [clock, setClock] = useState(() => Date.now());

  // 起動したら、その環境を見る
  useEffect(() => {
    if (launched) setWorkspace(launched);
  }, [launched, view.run?.id]);

  useEffect(() => {
    listEnvironments()
      .then((names) => {
        setEnvironments(names);
        // 何も選んでいなければ、最後に起動した環境を見る
        setWorkspace((current) => current ?? names[0] ?? null);
      })
      .catch(() => setEnvironments([]));
  }, [view.run?.id]);

  // 使える状態か失敗するまで、数秒ごとに読み直す
  useEffect(() => {
    setProgress(null);
    setError(null);
    if (!workspace) return;
    let cancelled = false;
    let timer: number | undefined;

    const load = () => {
      getProgress(workspace)
        .then((p) => {
          if (cancelled) return;
          setProgress(p);
          setFetchedAt(Date.now());
          setError(null);
          if (!isFinished(p)) timer = window.setTimeout(load, POLL_MS);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setError(e instanceof ApiError && e.status === 404 ? t("progress.noRecord", { workspace }) : t("progress.loadFailed", { error: e.message }));
          // 起動した直後で apply がまだ始まっていないこともあるので、読み直しは続ける
          if (!(e instanceof ApiError && e.status === 404)) timer = window.setTimeout(load, POLL_MS);
        });
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [workspace, view.run?.id, t]);

  // 経過時間を1秒ごとに進める
  useEffect(() => {
    if (!progress || isFinished(progress)) return;
    const id = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [progress]);

  const options = useMemo(() => Array.from(new Set([...(launched ? [launched] : []), ...environments])), [launched, environments]);
  const duration = (sec: number) => {
    const d = splitDuration(sec);
    if (d.hours) return t("progress.duration.hours", d);
    if (d.minutes) return t("progress.duration.minutes", d);
    return t("progress.duration.seconds", d);
  };

  const sinceFetch = progress ? Math.max(0, (clock - fetchedAt) / 1000) : 0;
  // サーバーの時刻に合わせた今。段階ごとの経過時間に使う
  const serverNow = progress ? Date.parse(progress.now) + sinceFetch * 1000 : clock;
  const times = progress ? tick(progress, sinceFetch) : null;

  return (
    <div className="progress-page">
      <section className="card">
        <div className="progress-header">
          <h2>{t("progress.heading")}</h2>
          <label className="field inline">
            <span>{t("progress.environment")}</span>
            <select value={workspace ?? ""} onChange={(e) => setWorkspace(e.target.value || null)}>
              {!options.length && <option value="">{t("progress.noEnvironment")}</option>}
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!workspace && <p className="muted">{t("progress.empty")}</p>}
        {error && <p className={progress ? "warning" : "muted"}>{error}</p>}

        {progress && (
          <>
            <p className="muted small">
              {t("progress.summary", {
                model: progress.model ?? "?",
                region: progress.region ?? "?",
                storage: t(`region.storageNames.${progress.model_storage}`),
                purchase: progress.purchase_option ? t(`progress.purchase.${progress.purchase_option}`) : "?",
              })}
            </p>

            <div className="progress-times">
              <div>
                <span className="muted small">{t(progress.state === "ready" ? "progress.tookTime" : "progress.elapsed")}</span>
                <strong>{times?.elapsed == null ? "—" : duration(times.elapsed)}</strong>
              </div>
              {progress.state !== "ready" && progress.state !== "failed" && (
                <div>
                  <span className="muted small">{t("progress.remaining")}</span>
                  <strong>{times?.remaining == null ? "—" : times.overdue ? t("progress.overdue") : t("progress.about", { time: duration(times.remaining) })}</strong>
                </div>
              )}
              <div>
                <span className="muted small">{t("progress.state")}</span>
                <span className={`badge ${badgeClass(progress.state)}`}>{t(`progress.states.${progress.state}`)}</span>
              </div>
            </div>
            {times?.overdue && <p className="muted small">{t("progress.overdueHelp")}</p>}

            <ol className="stages">
              {progress.stages.map((s) => (
                <StageRow key={s.id} stage={s} progress={progress} now={serverNow} duration={duration} />
              ))}
            </ol>
            <p className="muted small">{t("progress.estimateNote")}</p>
            {progress.warnings.map((w) => (
              <p key={w} className="warning small">
                {w}
              </p>
            ))}
          </>
        )}
      </section>

      {progress?.error && <FailureCard progress={progress} onShowTerraform={showTerraform} />}
      {progress?.state === "ready" && <ConnectionCard progress={progress} />}
    </div>
  );
}

function badgeClass(state: Progress["state"]) {
  if (state === "ready") return "succeeded";
  if (state === "failed") return "failed";
  return "running";
}

function StageRow({ stage, progress, now, duration }: { stage: ProgressStage; progress: Progress; now: number; duration: (sec: number) => string }) {
  const { t } = useTranslation();
  const sec = stage.status === "pending" ? null : stageSec(stage.started_at, stage.status === "active" ? null : stage.finished_at, now);
  const pull = stage.id === "pulling_model" && stage.status === "active" ? progress.pull : null;
  const gb = (bytes: number) => (bytes / 1e9).toFixed(1);

  return (
    <li className={`stage ${stage.status}`} aria-current={stage.status === "active" ? "step" : undefined}>
      <span className="stage-icon" aria-hidden="true">
        {ICON[stage.status]}
      </span>
      <div className="stage-body">
        <div className="stage-title">
          <strong>{t(`progress.stages.${stage.id}`)}</strong>
          <span className="sr-only">{t(`progress.stageStatus.${stage.status}`)}</span>
          {sec != null && stage.id !== "ready" && <span className="muted small">{duration(sec)}</span>}
        </div>
        {stage.status === "active" && <p className="muted small">{t(`progress.stageHelp.${stage.id}`)}</p>}
        {pull && (
          <div className="pull">
            <div className="pull-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pull.percent}>
              <div style={{ width: `${Math.min(pull.percent, 100)}%` }} />
            </div>
            <span className="small">
              {t("progress.pullPercent", { percent: pull.percent.toFixed(1), done: gb(pull.completed_bytes), total: gb(pull.total_bytes) })}
            </span>
          </div>
        )}
        {stage.id === "preparing_storage" && stage.status === "done" && progress.cache?.hit != null && (
          <p className="muted small">{t(progress.cache.hit ? "progress.cacheHit" : "progress.cacheMiss")}</p>
        )}
        {stage.id === "verifying" && stage.status === "done" && progress.verify && (
          <p className="muted small">{t("progress.firstResponse", { sec: progress.verify.first_response_sec })}</p>
        )}
      </div>
    </li>
  );
}

function FailureCard({ progress, onShowTerraform }: { progress: Progress; onShowTerraform: () => void }) {
  const { t } = useTranslation();
  const e = progress.error!;
  const c = progress.connection;

  return (
    <section className="card failure" aria-live="polite">
      <h2>{t("progress.failedHeading", { stage: t(`progress.stages.${e.stage}`) })}</h2>
      {e.message && <p className="error">{e.message}</p>}
      {e.terraform_log ? (
        <>
          <p className="muted small">{t("progress.terraformLog")}</p>
          <pre className="log short">{e.terraform_log.join("\n")}</pre>
          <button className="secondary" onClick={onShowTerraform}>
            {t("progress.showTerraform")}
          </button>
        </>
      ) : (
        <>
          <p className="small">{t("progress.instanceLogHelp", { path: e.log_path ?? c?.bootstrap_log_path ?? "/var/log/llm-launchpad-bootstrap.log" })}</p>
          {c?.ssm_session_command && <CommandBlock label={t("progress.openShell")} command={c.ssm_session_command} />}
          <CommandBlock label={t("progress.tailLog")} command={tailLogCommand(e.log_path ?? c?.bootstrap_log_path ?? "/var/log/llm-launchpad-bootstrap.log")} />
          <p className="muted small">{t("progress.failedCost")}</p>
        </>
      )}
    </section>
  );
}

function ConnectionCard({ progress }: { progress: Progress }) {
  const { t, i18n } = useTranslation();
  const c = progress.connection;
  if (!c) return <section className="card"><p className="muted">{t("progress.connectionUnknown")}</p></section>;
  const endpoint = c.ollama_endpoint ?? "http://localhost:11434";
  const stopAt = c.auto_stop?.stop_at ? new Date(c.auto_stop.stop_at).toLocaleString(i18n.language) : null;

  return (
    <section className="card connection" aria-live="polite">
      <h2>{t("progress.connectionHeading")}</h2>
      <dl className="launch-summary">
        <div>
          <dt>{t("progress.endpoint")}</dt>
          <dd>
            <code>{endpoint}</code>
          </dd>
        </div>
        {c.instance_id && (
          <div>
            <dt>{t("progress.instance")}</dt>
            <dd>
              <code>{c.instance_id}</code> {c.instance_type && `(${c.instance_type}, ${c.availability_zone ?? ""})`}
            </dd>
          </div>
        )}
        {c.auto_stop && (
          <div>
            <dt>{t("progress.autoStop")}</dt>
            <dd>
              {c.auto_stop.idle_stop_minutes > 0 ? t("progress.idleStop", { minutes: c.auto_stop.idle_stop_minutes }) : t("progress.idleStopOff")}
              {stopAt && ` · ${t("progress.stopAt", { at: stopAt })}`}
            </dd>
          </div>
        )}
      </dl>

      {c.access_mode !== "direct" && c.ssm_port_forward_command && (
        <CommandBlock label={t("progress.portForward")} help={t("progress.portForwardHelp")} command={c.ssm_port_forward_command} />
      )}
      {c.curl_example && <CommandBlock label={t("progress.curl")} command={c.curl_example} />}
      {c.openai_api_example && <CommandBlock label={t("progress.openaiCurl")} command={c.openai_api_example} />}
      {progress.model && <CommandBlock label={t("progress.openaiPython")} help={t("progress.openaiPythonHelp")} command={openaiPythonExample(endpoint, progress.model, t("progress.samplePrompt"))} />}
      {c.ssm_session_command && <CommandBlock label={t("progress.openShell")} command={c.ssm_session_command} />}
    </section>
  );
}

function CommandBlock({ label, help, command }: { label: string; help?: string; command: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="command-block">
      <div className="command-label">
        <span className="small">{label}</span>
        <button className="chip" onClick={copy}>
          {copied ? t("progress.copied") : t("progress.copy")}
        </button>
      </div>
      {help && <p className="muted small">{help}</p>}
      <pre className="command">{command}</pre>
    </div>
  );
}
