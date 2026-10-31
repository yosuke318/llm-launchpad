import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  destroyBedrockEnvironment,
  getBenchmarkJob,
  launchBedrock,
  listBedrockEnvironments,
  listBedrockModels,
  sendBedrockChat,
  startBedrockBenchmark,
  type BedrockChatMessage,
  type BedrockEnvironment,
  type BedrockModel,
  type BedrockModels,
  type BenchmarkJob,
} from "../api";
import { bedrockDefaultEnvId, bedrockEnvBusy, bedrockEnvState, type BedrockEnvState } from "../bedrock";
import { isValidEnvId } from "../launch";
import { useRun } from "../runContext";

// Bedrock のモデルは、今は東京(jp. の推論プロファイル)だけ
const REGION = "ap-northeast-1";
const POLL_MS = 5000;

// 状態ごとのバッジの色(App.css の .badge)
const BADGE: Record<BedrockEnvState, string> = {
  creating: "running",
  destroying: "running",
  ready: "succeeded",
  failed: "failed",
  destroyFailed: "failed",
  unknown: "stopped",
};

// 推論エンジンに Bedrock を選んだとき。モデルを選んで環境を作り、作った環境で話したり測ったり消したりする
export default function BedrockPanel({ engineSelect }: { engineSelect: ReactNode }) {
  const { t, i18n } = useTranslation();
  const [catalog, setCatalog] = useState<BedrockModels | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [envs, setEnvs] = useState<BedrockEnvironment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const usd = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    [i18n.language],
  );
  const money = (v: number | null | undefined) => (v == null ? t("region.unknown") : usd.format(v));

  useEffect(() => {
    listBedrockModels(REGION)
      .then((c) => {
        setCatalog(c);
        setSelectedId(c.default);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const reload = useCallback(() => {
    listBedrockEnvironments()
      .then((r) => setEnvs(r.environments))
      .catch((e: Error) => setError(e.message));
  }, []);
  useEffect(reload, [reload]);

  // 作っている途中や消している途中は、終わるまで読み直す
  const busy = bedrockEnvBusy(envs);
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [busy, reload]);

  const selected = catalog?.models.find((m) => m.id === selectedId) ?? null;

  return (
    <div className="bedrock">
      <section className="card">
        <div className="filters">{engineSelect}</div>
        <h2>{t("bedrock.heading")}</h2>
        <p className="muted small">{t("bedrock.lead")}</p>
        {error && <p className="error">{t("bedrock.loadFailed", { error })}</p>}
        {catalog && (
          <>
            <div className="table-wrap">
              <table className="model-table">
                <thead>
                  <tr>
                    <th>{t("models.columns.model")}</th>
                    <th className="num">{t("bedrock.inputPrice")}</th>
                    <th className="num">{t("bedrock.outputPrice")}</th>
                  </tr>
                </thead>
                <tbody>
                  {catalog.models.map((m) => (
                    <tr key={m.id} className={m.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(m.id)}>
                      <td>
                        <label>
                          <input type="radio" name="bedrock-model" checked={m.id === selectedId} onChange={() => setSelectedId(m.id)} /> {m.name}
                        </label>
                        <div className="muted small">
                          <code>{m.id}</code>
                        </div>
                      </td>
                      <td className="num">{t("bedrock.perMillion", { price: money(m.input_usd_per_million) })}</td>
                      <td className="num">{t("bedrock.perMillion", { price: money(m.output_usd_per_million) })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="small">{t("bedrock.endpointHourly", { price: money(catalog.endpoint_hourly_usd) })}</p>
            {catalog.warnings.length > 0 && (
              <ul className="warning small">
                {catalog.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            <div className="confirm-row">
              <button className="primary" disabled={!selected} onClick={() => setConfirming(true)}>
                {t("region.confirm")}
              </button>
            </div>
          </>
        )}
      </section>

      <BedrockEnvironments envs={envs} reload={reload} />

      {confirming && selected && catalog && (
        <BedrockLaunchDialog model={selected} endpointHourly={catalog.endpoint_hourly_usd} money={money} onClose={() => setConfirming(false)} onLaunched={reload} />
      )}
    </div>
  );
}

// 起動の確認。GPUインスタンスとの違い(トークン課金、起動待ちがない)と料金を出し、Yes で terraform/bedrock を apply する
function BedrockLaunchDialog({
  model,
  endpointHourly,
  money,
  onClose,
  onLaunched,
}: {
  model: BedrockModel;
  endpointHourly: number | null;
  money: (v: number | null | undefined) => string;
  onClose: () => void;
  onLaunched: () => void;
}) {
  const { t } = useTranslation();
  const { start } = useRun();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [envId, setEnvId] = useState(() => bedrockDefaultEnvId(model.id));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const variables = { model: model.id, region: REGION };
  const envIdOk = isValidEnvId(envId);

  async function yes() {
    setSubmitting(true);
    setError(null);
    try {
      start(await launchBedrock(envId, variables));
      onLaunched();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? t("terraform.busy") : (err as Error).message);
      setSubmitting(false);
    }
  }

  const rows = ["billing", "startup", "hardware", "network", "stop"] as const;

  return (
    <dialog ref={dialogRef} className="launch-dialog" aria-labelledby="bedrock-launch-title" onCancel={onClose}>
      <h2 id="bedrock-launch-title">{t("bedrock.launchTitle")}</h2>
      <p className="muted small">{t("bedrock.launchLead")}</p>

      <div className="launch-total">
        <span className="muted small">{t("bedrock.hourlyWhileExists")}</span>
        <strong>{t("region.perHour", { price: money(endpointHourly) })}</strong>
        <span className="muted small">
          {t("bedrock.tokenPrices", { input: money(model.input_usd_per_million), output: money(model.output_usd_per_million) })}
        </span>
      </div>

      <dl className="launch-summary">
        <div>
          <dt>{t("launch.model")}</dt>
          <dd>
            {model.name} <code>{model.id}</code>
          </dd>
        </div>
        <div>
          <dt>{t("launch.region")}</dt>
          <dd>{REGION}</dd>
        </div>
      </dl>

      <table className="model-table small">
        <caption className="muted small">{t("bedrock.compareCaption")}</caption>
        <thead>
          <tr>
            <th />
            <th>{t("bedrock.compare.ec2")}</th>
            <th>{t("bedrock.compare.bedrock")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row}>
              <th>{t(`bedrock.compare.rows.${row}.label`)}</th>
              <td>{t(`bedrock.compare.rows.${row}.ec2`)}</td>
              <td>{t(`bedrock.compare.rows.${row}.bedrock`)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <label className="field">
        <span>{t("launch.envId")}</span>
        <input type="text" value={envId} onChange={(ev) => setEnvId(ev.target.value.trim())} spellCheck={false} />
        <span className={envIdOk ? "muted small" : "error small"}>{envIdOk ? t("launch.envIdHelp") : t("launch.envIdInvalid")}</span>
      </label>

      <details className="small">
        <summary>{t("launch.tfvars", { path: `terraform/bedrock/tfvars/${envId}.tfvars.json` })}</summary>
        <pre className="tfvars">{JSON.stringify(variables, null, 2)}</pre>
      </details>

      <p className="warning small">{t("bedrock.modelAccess")}</p>
      {error && <p className="error">{t("launch.failed", { error })}</p>}

      <div className="dialog-buttons">
        <button className="secondary" onClick={onClose} disabled={submitting}>
          {t("launch.no")}
        </button>
        <button className="primary" onClick={yes} disabled={!envIdOk || submitting}>
          {submitting ? t("launch.starting") : t("launch.yes")}
        </button>
      </div>
    </dialog>
  );
}

function BedrockEnvironments({ envs, reload }: { envs: BedrockEnvironment[]; reload: () => void }) {
  const { t } = useTranslation();
  const { start, showTerraform } = useRun();
  const [chatting, setChatting] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function destroy(env: BedrockEnvironment) {
    if (!window.confirm(t("bedrock.destroyConfirm", { workspace: env.workspace }))) return;
    setActionError(null);
    try {
      start(await destroyBedrockEnvironment(env.workspace));
      if (chatting === env.workspace) setChatting(null);
      reload();
    } catch (e) {
      setActionError(e instanceof ApiError && e.status === 409 ? t("terraform.busy") : (e as Error).message);
    }
  }

  return (
    <section className="card">
      <h2>{t("bedrock.environmentsHeading")}</h2>
      {envs.length === 0 ? (
        <p className="muted small">{t("bedrock.noEnvironments")}</p>
      ) : (
        <ul className="environment-list">
          {envs.map((env) => {
            const state = bedrockEnvState(env);
            return (
              <li key={env.workspace} className="environment">
                <div className="environment-title">
                  <strong>{env.workspace}</strong>
                  <span className={`badge ${BADGE[state]}`}>{t(`bedrock.states.${state}`)}</span>
                  <span className="muted small">
                    {env.model_name} · {env.region}
                    {env.availability_zone && ` · ${env.availability_zone}`}
                  </span>
                </div>
                {env.function_name && (
                  <p className="muted small">
                    Lambda <code>{env.function_name}</code>
                    {env.vpc_endpoint_ids?.length ? (
                      <>
                        {" "}
                        · {t("bedrock.endpoint")} <code>{env.vpc_endpoint_ids.join(", ")}</code>
                      </>
                    ) : null}
                  </p>
                )}
                <div className="environment-actions">
                  {state === "ready" && (
                    <button className="secondary" onClick={() => setChatting(chatting === env.workspace ? null : env.workspace)}>
                      {t("bedrock.chat")}
                    </button>
                  )}
                  {(state === "creating" || state === "destroying" || state === "failed" || state === "destroyFailed") && (
                    <button className="secondary" onClick={showTerraform}>
                      {t("environments.showTerraform")}
                    </button>
                  )}
                  {state !== "creating" && state !== "destroying" && (
                    <button className="danger" onClick={() => destroy(env)}>
                      {t("environments.destroy")}
                    </button>
                  )}
                </div>
                {state === "ready" && <BedrockBenchmark workspace={env.workspace} />}
                {chatting === env.workspace && state === "ready" && <BedrockChat workspace={env.workspace} />}
              </li>
            );
          })}
        </ul>
      )}
      {actionError && <p className="error small">{actionError}</p>}
      <p className="muted small">{t("bedrock.destroyNote")}</p>
    </section>
  );
}

// GPUインスタンスと同じプロンプトを、Lambda から流して測る。結果はベンチマークのタブの履歴にも入る
function BedrockBenchmark({ workspace }: { workspace: string }) {
  const { t, i18n } = useTranslation();
  const [runs, setRuns] = useState(3);
  const [job, setJob] = useState<BenchmarkJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const n = (v: number | null | undefined) => (v == null ? "—" : new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 3 }).format(v));
  const usd = (v: number | null | undefined) =>
    v == null ? "—" : new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", maximumFractionDigits: 4 }).format(v);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = window.setTimeout(() => {
      getBenchmarkJob(job.id)
        .then(setJob)
        .catch((e: Error) => setError(e.message));
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [job]);

  async function run() {
    setError(null);
    try {
      setJob(await startBedrockBenchmark(workspace, runs));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const result = job?.result;
  return (
    <div className="bedrock-bench">
      <label className="field inline">
        <span>{t("benchmark.runs")}</span>
        <input type="number" min={1} max={10} value={runs} onChange={(e) => setRuns(Number(e.target.value))} />
      </label>
      <button className="secondary" onClick={run} disabled={job?.status === "running" || runs < 1 || runs > 10}>
        {job?.status === "running" ? t("benchmark.running") : t("benchmark.run")}
      </button>
      {job?.status === "failed" && <p className="error small">{job.error}</p>}
      {error && <p className="error small">{error}</p>}
      {result && (
        <p className="small">
          {t("bedrock.benchResult", { ttft: n(result.ttft_sec), tps: n(result.output_tokens_per_sec), cost: usd(result.usd_per_million_tokens) })}
        </p>
      )}
    </div>
  );
}

function BedrockChat({ workspace }: { workspace: string }) {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<BedrockChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const next = [...messages, { role: "user" as const, content: input.trim() }];
    setMessages(next);
    setInput("");
    setSending(true);
    setError(null);
    try {
      const reply = await sendBedrockChat(workspace, next);
      setMessages([...next, { role: "assistant", content: reply.content }]);
    } catch (e) {
      setError((e as Error).message);
      // 送れなかった発言は、入力欄に戻す
      setMessages(messages);
      setInput(next[next.length - 1].content);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="bedrock-chat">
      {messages.length > 0 && (
        <ol className="chat-log">
          {messages.map((m, i) => (
            <li key={i} className={`chat-message ${m.role}`}>
              <span className="muted small">{t(`benchmark.roles.${m.role}`)}</span>
              <div className="chat-content">{m.content}</div>
            </li>
          ))}
        </ol>
      )}
      {error && <p className="error small">{error}</p>}
      <div className="chat-input">
        <textarea
          value={input}
          rows={3}
          aria-label={t("benchmark.chatInputLabel")}
          placeholder={t("benchmark.chatPlaceholder")}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && input.trim() && !sending) void send();
          }}
        />
        <div className="dialog-buttons">
          <button className="primary" onClick={send} disabled={sending || !input.trim()}>
            {sending ? t("benchmark.chatSending") : t("benchmark.chatSend")}
          </button>
        </div>
      </div>
      <p className="muted small">{t("bedrock.chatNote")}</p>
    </div>
  );
}
