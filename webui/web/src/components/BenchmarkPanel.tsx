import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  BENCHMARKS_CSV_URL,
  BENCHMARKS_JSON_URL,
  getBenchmarkJob,
  listBenchmarks,
  listEnvironments,
  sendChat,
  startBenchmark,
  type Benchmark,
  type BenchmarkJob,
  type ChatMessage,
  type ChatReply,
} from "../api";
import { BENCHMARK_POLL_MS, isValidRuns, MAX_RUNS, MIN_RUNS, nextMessages, signed } from "../benchmark";

// ベンチマーク。起動した環境のインスタンスで決まったプロンプトを流して、TTFT、tok/s、VRAM、$/100万トークンを測る。
// 結果は履歴として残し、CSV と JSON で出せる。下の簡単なチャットで、モデルに話しかけて試せる
export default function BenchmarkPanel() {
  const { t, i18n } = useTranslation();
  const [environments, setEnvironments] = useState<string[]>([]);
  const [workspace, setWorkspace] = useState<string>("");
  const [runs, setRuns] = useState(3);
  const [job, setJob] = useState<BenchmarkJob | null>(null);
  const [history, setHistory] = useState<Benchmark[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listEnvironments()
      .then((names) => {
        setEnvironments(names);
        setWorkspace((current) => current || names[0] || "");
      })
      .catch((e: Error) => setError(e.message));
    listBenchmarks()
      .then(setHistory)
      .catch((e: Error) => setError(e.message));
  }, []);

  // 計測が終わるまで読み直す
  const jobId = job?.status === "running" ? job.id : null;
  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer: number | undefined;
    const load = () => {
      getBenchmarkJob(jobId)
        .then((j) => {
          if (cancelled) return;
          setJob(j);
          if (j.status === "running") timer = window.setTimeout(load, BENCHMARK_POLL_MS);
          else listBenchmarks().then(setHistory).catch(() => undefined);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setError(e.message);
          timer = window.setTimeout(load, BENCHMARK_POLL_MS);
        });
    };
    timer = window.setTimeout(load, BENCHMARK_POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobId]);

  async function run() {
    setError(null);
    try {
      setJob(await startBenchmark(workspace, runs));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const num = useMemo(() => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }), [i18n.language]);
  const usd = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    [i18n.language],
  );
  const n = (v: number | null | undefined) => (v == null ? "—" : num.format(v));
  const money = (v: number | null | undefined) => (v == null ? "—" : usd.format(v));
  const running = job?.status === "running";
  const runsOk = isValidRuns(runs);

  return (
    <div className="benchmark-page">
      <section className="card">
        <h2>{t("benchmark.heading")}</h2>
        <p className="muted small">{t("benchmark.lead")}</p>
        <div className="benchmark-controls">
          <label className="field inline">
            <span>{t("progress.environment")}</span>
            <select value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
              {!environments.length && <option value="">{t("progress.noEnvironment")}</option>}
              {environments.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="field inline">
            <span>{t("benchmark.runs")}</span>
            <input type="number" min={MIN_RUNS} max={MAX_RUNS} value={runs} onChange={(e) => setRuns(Number(e.target.value))} />
          </label>
          <button className="primary" onClick={run} disabled={!workspace || running || !runsOk}>
            {running ? t("benchmark.running") : t("benchmark.run")}
          </button>
        </div>
        {!runsOk && <p className="error small">{t("benchmark.runsInvalid", { min: MIN_RUNS, max: MAX_RUNS })}</p>}
        <p className="muted small">{t("benchmark.howItRuns")}</p>
        {error && <p className="error">{error}</p>}
        {running && (
          <p className="small" aria-live="polite">
            {t("benchmark.waiting", { workspace: job!.workspace, model: job!.model })}
          </p>
        )}
        {job?.status === "failed" && <p className="error">{job.error}</p>}
        {job?.warnings.map((w) => (
          <p key={w} className="warning small">
            {w}
          </p>
        ))}
        {job?.status === "succeeded" && job.result && (
          <dl className="launch-summary benchmark-result" aria-live="polite">
            <div>
              <dt>{t("benchmark.columns.ttft")}</dt>
              <dd>{n(job.result.ttft_sec)}</dd>
            </div>
            <div>
              <dt>{t("benchmark.columns.tokensPerSec")}</dt>
              <dd>{n(job.result.output_tokens_per_sec)}</dd>
            </div>
            <div>
              <dt>{t("benchmark.columns.vram")}</dt>
              <dd>
                {t("benchmark.vramDetail", {
                  measured: n(job.result.vram_measured_gib),
                  estimate: n(job.result.vram_estimate_gib),
                  diff: job.result.vram_diff_gib == null ? "—" : signed(job.result.vram_diff_gib),
                })}
              </dd>
            </div>
            <div>
              <dt>{t("benchmark.columns.costPerMillion")}</dt>
              <dd>
                {money(job.result.usd_per_million_tokens)}
                {job.result.hourly_usd != null && <span className="muted small"> ({t("region.perHour", { price: money(job.result.hourly_usd) })})</span>}
              </dd>
            </div>
          </dl>
        )}
      </section>

      <section className="card">
        <div className="progress-header">
          <h2>{t("benchmark.historyHeading")}</h2>
          <div className="environment-actions">
            <a className="chip" href={BENCHMARKS_CSV_URL} download="benchmarks.csv">
              {t("benchmark.downloadCsv")}
            </a>
            <a className="chip" href={BENCHMARKS_JSON_URL} download="benchmarks.json">
              {t("benchmark.downloadJson")}
            </a>
          </div>
        </div>
        {history.length === 0 ? (
          <p className="muted small">{t("benchmark.noHistory")}</p>
        ) : (
          <div className="table-wrap">
            <table className="model-table">
              <thead>
                <tr>
                  <th>{t("benchmark.columns.measuredAt")}</th>
                  <th>{t("benchmark.columns.model")}</th>
                  <th>{t("benchmark.columns.instance")}</th>
                  <th className="num">{t("benchmark.columns.ttft")}</th>
                  <th className="num">{t("benchmark.columns.tokensPerSec")}</th>
                  <th className="num">{t("benchmark.columns.vramMeasured")}</th>
                  <th className="num">{t("benchmark.columns.vramDiff")}</th>
                  <th className="num">{t("benchmark.columns.costPerMillion")}</th>
                </tr>
              </thead>
              <tbody>
                {history.map((b) => (
                  <tr key={b.id}>
                    <td>{new Date(b.measured_at).toLocaleString(i18n.language)}</td>
                    <td>
                      <code>{b.model}</code>
                    </td>
                    <td>
                      <code>{b.instance_type}</code>
                      {b.purchase_option && ` · ${t(`progress.purchase.${b.purchase_option}`)}`}
                    </td>
                    <td className="num">{n(b.ttft_sec)}</td>
                    <td className="num">{n(b.output_tokens_per_sec)}</td>
                    <td className="num">{n(b.vram_measured_gib)}</td>
                    <td className="num">{b.vram_diff_gib == null ? "—" : signed(b.vram_diff_gib)}</td>
                    <td className="num">{money(b.usd_per_million_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="muted small">{t("benchmark.note")}</p>
      </section>

      {workspace && <ChatCard workspace={workspace} />}
    </div>
  );
}

function ChatCard({ workspace }: { workspace: string }) {
  const { t } = useTranslation();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [stats, setStats] = useState<Record<number, ChatReply>>({});
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 環境を変えたら、会話をやり直す
  useEffect(() => {
    setMessages([]);
    setStats({});
    setError(null);
  }, [workspace]);

  async function send() {
    const next = nextMessages(messages, input);
    if (!next) return;
    setMessages(next);
    setInput("");
    setSending(true);
    setError(null);
    try {
      const reply = await sendChat(workspace, next);
      setStats((s) => ({ ...s, [next.length]: reply }));
      setMessages([...next, reply.message]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <section className="card">
      <h2>{t("benchmark.chatHeading")}</h2>
      <p className="muted small">{t("benchmark.chatLead")}</p>
      {messages.length > 0 && (
        <ol className="chat-log">
          {messages.map((m, i) => (
            <li key={i} className={`chat-message ${m.role}`}>
              <span className="muted small">{t(`benchmark.roles.${m.role}`)}</span>
              <div className="chat-content">{m.content}</div>
              {stats[i] && (
                <span className="muted small">
                  {t("benchmark.chatStats", { sec: stats[i].total_sec, tps: stats[i].output_tokens_per_sec ?? "—" })}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
      {error && <p className="error small">{error}</p>}
      <div className="chat-input">
        <textarea
          value={input}
          rows={3}
          placeholder={t("benchmark.chatPlaceholder")}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void send();
          }}
        />
        <div className="dialog-buttons">
          <button className="secondary" onClick={() => {
              setMessages([]);
              setStats({});
            }} disabled={sending || !messages.length}>
            {t("benchmark.chatClear")}
          </button>
          <button className="primary" onClick={send} disabled={sending || !input.trim()}>
            {sending ? t("benchmark.chatSending") : t("benchmark.chatSend")}
          </button>
        </div>
      </div>
    </section>
  );
}
