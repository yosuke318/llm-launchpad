import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  cancelComparison,
  comparisonCsvUrl,
  getCatalog,
  getComparison,
  getPreflight,
  getRegionPlan,
  listComparisons,
  listModels,
  listRegions,
  startComparison,
  type Catalog,
  type Comparison,
  type ComparisonConfig,
  type ModelEstimate,
  type NetworkMode,
  type Preflight,
  type Region,
  type RegionPlan,
} from "../api";
import { MAX_RUNS, MIN_RUNS, isValidRuns } from "../benchmark";
import {
  COMPARISON_POLL_MS,
  MAX_CONFIGS,
  MIN_CONFIGS,
  PURCHASE_OPTIONS,
  STORAGES,
  bestValue,
  comparisonEstimate,
  configDurationSec,
  configHourly,
  configProblems,
  defaultConfigs,
  isActive,
  nextConfig,
} from "../compare";
import { minutes } from "../cost";
import { validIdleStop } from "../launch";

const DEFAULT_REGION = "ap-northeast-1";

// 比較モード。同じモデルを、インスタンス型・モデルの置き場所・購入方法を変えた構成で1つずつ順に起動して測り、比較表を出す。
// 費用を抑えるため、構成ごとに 起動 → 計測 → 削除 してから次へ進む(同時に動かす環境は1つだけ)
export default function ComparisonPanel() {
  const { t, i18n } = useTranslation();
  const [comparisons, setComparisons] = useState<Comparison[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listComparisons()
      .then((list) => {
        setComparisons(list);
        setSelectedId((current) => current ?? list[0]?.id ?? null);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const selected = comparisons.find((c) => c.id === selectedId) ?? null;
  const running = comparisons.some((c) => c.status === "running");

  // 動いている比較を読み直す
  const runningId = selected?.status === "running" ? selected.id : null;
  useEffect(() => {
    if (!runningId) return;
    let cancelled = false;
    let timer: number | undefined;
    const load = () => {
      getComparison(runningId)
        .then((c) => {
          if (cancelled) return;
          setComparisons((list) => list.map((x) => (x.id === c.id ? c : x)));
          if (c.status === "running") timer = window.setTimeout(load, COMPARISON_POLL_MS);
        })
        .catch((e: Error) => {
          if (cancelled) return;
          setError(e.message);
          timer = window.setTimeout(load, COMPARISON_POLL_MS);
        });
    };
    timer = window.setTimeout(load, COMPARISON_POLL_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [runningId]);

  function started(c: Comparison) {
    setComparisons((list) => [c, ...list]);
    setSelectedId(c.id);
  }

  return (
    <div className="benchmark-page">
      <ComparisonForm disabled={running} onStarted={started} />
      {error && <p className="error">{error}</p>}
      {comparisons.length > 0 && (
        <section className="card">
          <div className="progress-header">
            <h2>{t("compare.resultHeading")}</h2>
            <label className="field inline">
              <span>{t("compare.pick")}</span>
              <select value={selectedId ?? ""} onChange={(e) => setSelectedId(e.target.value)}>
                {comparisons.map((c) => (
                  <option key={c.id} value={c.id}>
                    {new Date(c.started_at).toLocaleString(i18n.language)} · {c.model} · {t(`compare.status.${c.status}`)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {selected && (
            <ComparisonResult
              comparison={selected}
              onChange={(c) => setComparisons((list) => list.map((x) => (x.id === c.id ? c : x)))}
            />
          )}
        </section>
      )}
    </div>
  );
}

function useFormatters() {
  const { t, i18n } = useTranslation();
  return useMemo(() => {
    const num = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 });
    const usd = new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 });
    return {
      n: (v: number | null | undefined) => (v == null ? "—" : num.format(v)),
      money: (v: number | null | undefined) => (v == null ? t("region.unknown") : usd.format(v)),
      mins: (sec: number | null | undefined) => (sec == null ? "—" : t("region.minutes", { count: minutes(sec) })),
    };
  }, [i18n.language, t]);
}

function ComparisonForm({ disabled, onStarted }: { disabled: boolean; onStarted: (c: Comparison) => void }) {
  const { t } = useTranslation();
  const { money, mins } = useFormatters();
  const dialogRef = useRef<HTMLDialogElement>(null);

  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [regions, setRegions] = useState<Region[]>([]);
  const [models, setModels] = useState<ModelEstimate[]>([]);
  const [model, setModel] = useState("");
  const [contextLength, setContextLength] = useState(8192);
  const [kvCacheType, setKvCacheType] = useState("f16");
  const [region, setRegion] = useState(DEFAULT_REGION);
  const [networkMode, setNetworkMode] = useState<NetworkMode>("public");
  const [runs, setRuns] = useState(3);
  const [idleStopMinutes, setIdleStopMinutes] = useState(30);
  const [maxLifetimeHours, setMaxLifetimeHours] = useState(2);
  const [configs, setConfigs] = useState<ComparisonConfig[]>([]);
  const [plans, setPlans] = useState<Record<string, RegionPlan | undefined>>({});
  const [preflights, setPreflights] = useState<Record<string, Preflight | undefined>>({});
  const [warnings, setWarnings] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCatalog()
      .then((c) => {
        setCatalog(c);
        setContextLength(c.defaults.context_length);
        setKvCacheType(c.defaults.kv_cache_type);
      })
      .catch((e: Error) => setError(e.message));
    listRegions()
      .then((r) => {
        setRegions(r.regions);
        setRegion(r.default);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!catalog) return;
    listModels({ context_length: contextLength, kv_cache_type: kvCacheType })
      .then((list) => {
        setModels(list);
        setModel((current) => (list.some((m) => m.model.tag === current) ? current : (list.find((m) => m.instance_type)?.model.tag ?? "")));
      })
      .catch((e: Error) => setError(e.message));
  }, [catalog, contextLength, kvCacheType]);

  const estimate = models.find((m) => m.model.tag === model) ?? null;
  const fitting = useMemo(() => estimate?.fitting_instance_types ?? [], [estimate]);

  // モデルやコンテキスト長を変えたら、収まる型で並べ直す
  const fittingKey = fitting.join(",");
  useEffect(() => {
    setConfigs((current) => {
      const kept = current.filter((c) => fitting.includes(c.instance_type));
      return kept.length >= MIN_CONFIGS ? kept : defaultConfigs(fitting);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fittingKey]);

  // 構成に出てくるインスタンス型ごとに、料金と事前チェックを読む
  const types = useMemo(() => [...new Set(configs.map((c) => c.instance_type))].sort(), [configs]);
  const typesKey = types.join(",");
  useEffect(() => {
    if (!estimate || !types.length) return;
    let cancelled = false;
    setWarnings([]);
    for (const instanceType of types) {
      getRegionPlan({ model: estimate.model.tag, region, context_length: estimate.vram_estimate.context_length, kv_cache_type: kvCacheType, instance_type: instanceType })
        .then((p) => {
          if (cancelled) return;
          setPlans((x) => ({ ...x, [instanceType]: p }));
          if (p.warnings.length) setWarnings((w) => [...new Set([...w, ...p.warnings])]);
        })
        .catch((e: Error) => !cancelled && setWarnings((w) => [...w, e.message]));
      getPreflight({ region, instance_type: instanceType })
        .then((p) => !cancelled && setPreflights((x) => ({ ...x, [instanceType]: p })))
        .catch((e: Error) => !cancelled && setWarnings((w) => [...w, e.message]));
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [estimate?.model.tag, estimate?.vram_estimate.context_length, kvCacheType, region, typesKey]);

  // リージョンやモデルを変えたら、前の料金と事前チェックは使わない
  useEffect(() => {
    setPlans({});
    setPreflights({});
  }, [region, model, contextLength, kvCacheType]);

  const problems = configProblems(configs, fitting, networkMode, plans, preflights);
  const loaded = types.every((type) => plans[type] && preflights[type]);
  const total = comparisonEstimate(configs, plans, networkMode, runs);
  const settingsOk = isValidRuns(runs) && validIdleStop(idleStopMinutes) && Number.isInteger(maxLifetimeHours) && maxLifetimeHours >= 1 && maxLifetimeHours <= 720;
  const ready =
    !!estimate && configs.length >= MIN_CONFIGS && configs.length <= MAX_CONFIGS && loaded && problems.every((p) => !p.length) && settingsOk && !disabled;
  const next = nextConfig(configs, fitting, networkMode);

  function update(index: number, change: Partial<ComparisonConfig>) {
    setConfigs((list) => list.map((c, i) => (i === index ? { ...c, ...change } : c)));
  }

  function openConfirm() {
    setError(null);
    dialogRef.current?.showModal();
  }

  async function yes() {
    if (!estimate) return;
    setSubmitting(true);
    setError(null);
    try {
      const c = await startComparison({
        model: estimate.model.tag,
        region,
        context_length: estimate.vram_estimate.context_length,
        kv_cache_type: kvCacheType,
        network_mode: networkMode,
        runs,
        idle_stop_minutes: idleStopMinutes,
        max_lifetime_hours: maxLifetimeHours,
        configs,
      });
      dialogRef.current?.close();
      onStarted(c);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="card comparison-form">
      <h2>{t("compare.heading")}</h2>
      <p className="muted small">{t("compare.lead")}</p>

      <div className="benchmark-controls">
        <label className="field inline">
          <span>{t("compare.model")}</span>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
              <option key={m.model.tag} value={m.model.tag} disabled={!m.instance_type}>
                {m.model.name} ({m.model.tag})
              </option>
            ))}
          </select>
        </label>
        <label className="field inline">
          <span>{t("models.contextLength")}</span>
          <select value={contextLength} onChange={(e) => setContextLength(Number(e.target.value))}>
            {catalog?.context_lengths.map((c) => (
              <option key={c} value={c}>
                {c.toLocaleString()}
              </option>
            ))}
          </select>
        </label>
        <label className="field inline">
          <span>{t("models.kvCacheType")}</span>
          <select value={kvCacheType} onChange={(e) => setKvCacheType(e.target.value)}>
            {catalog?.kv_cache_types.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label className="field inline">
          <span>{t("region.region")}</span>
          <select value={region} onChange={(e) => setRegion(e.target.value)}>
            {(regions.length ? regions : [{ code: DEFAULT_REGION, name: "" }]).map((r) => (
              <option key={r.code} value={r.code}>
                {r.code}
                {r.name && ` (${r.name})`}
              </option>
            ))}
          </select>
        </label>
        <label className="field inline">
          <span>{t("region.network")}</span>
          <select value={networkMode} onChange={(e) => setNetworkMode(e.target.value as NetworkMode)}>
            {(["public", "private"] as const).map((m) => (
              <option key={m} value={m}>
                {t(`region.networkNames.${m}`)}
              </option>
            ))}
          </select>
        </label>
      </div>
      {estimate?.context_capped && <p className="muted small">{t("compare.contextCapped", { max: estimate.model.max_context.toLocaleString() })}</p>}

      <div className="table-wrap" aria-busy={!loaded}>
        <table className="model-table">
          <thead>
            <tr>
              <th>#</th>
              <th>{t("compare.columns.instanceType")}</th>
              <th>{t("region.storage")}</th>
              <th>{t("compare.columns.purchase")}</th>
              <th className="num">{t("compare.columns.hourly")}</th>
              <th className="num">{t("compare.columns.duration")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {configs.map((c, i) => (
              <tr key={i}>
                <td>{i + 1}</td>
                <td>
                  <select aria-label={t("compare.columns.instanceType")} value={c.instance_type} onChange={(e) => update(i, { instance_type: e.target.value })}>
                    {fitting.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    aria-label={t("region.storage")}
                    value={c.model_storage}
                    onChange={(e) => update(i, { model_storage: e.target.value as ComparisonConfig["model_storage"] })}
                  >
                    {STORAGES.map((s) => (
                      <option key={s} value={s} disabled={networkMode === "private" && s === "download"}>
                        {t(`region.storageNames.${s}`)}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <select
                    aria-label={t("compare.columns.purchase")}
                    value={c.purchase_option}
                    onChange={(e) => update(i, { purchase_option: e.target.value as ComparisonConfig["purchase_option"] })}
                  >
                    {PURCHASE_OPTIONS.map((p) => (
                      <option key={p} value={p}>
                        {t(`progress.purchase.${p}`)}
                      </option>
                    ))}
                  </select>
                  {problems[i].map((p) => (
                    <p key={p} className="error small">
                      {t(`compare.problems.${p}`, { region })}
                    </p>
                  ))}
                </td>
                <td className="num">{plans[c.instance_type] ? money(configHourly(c, plans[c.instance_type], networkMode)) : "…"}</td>
                <td className="num">{plans[c.instance_type] ? mins(configDurationSec(c, plans[c.instance_type], runs)) : "…"}</td>
                <td>
                  <button className="secondary" onClick={() => setConfigs((list) => list.filter((_, j) => j !== i))} disabled={configs.length <= MIN_CONFIGS}>
                    {t("compare.remove")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="benchmark-controls">
        <button className="secondary" onClick={() => next && setConfigs((list) => [...list, next])} disabled={!next || configs.length >= MAX_CONFIGS}>
          {t("compare.add", { max: MAX_CONFIGS })}
        </button>
        <label className="field inline">
          <span>{t("benchmark.runs")}</span>
          <input type="number" min={MIN_RUNS} max={MAX_RUNS} value={runs} onChange={(e) => setRuns(Number(e.target.value))} />
        </label>
        <label className="field inline">
          <span>{t("launch.idleStop")}</span>
          <input type="number" min={0} max={1440} value={idleStopMinutes} onChange={(e) => setIdleStopMinutes(Number(e.target.value))} />
        </label>
        <label className="field inline">
          <span>{t("compare.maxLifetime")}</span>
          <input type="number" min={1} max={720} value={maxLifetimeHours} onChange={(e) => setMaxLifetimeHours(Number(e.target.value))} />
        </label>
      </div>
      {!settingsOk && <p className="error small">{t("compare.settingsInvalid", { min: MIN_RUNS, max: MAX_RUNS })}</p>}
      {warnings.map((w) => (
        <p key={w} className="warning small">
          {w}
        </p>
      ))}

      <div className="hourly-total">
        <span>{t("compare.total")}</span>
        <strong>{total.totalUsd == null ? t("region.unknown") : money(total.totalUsd)}</strong>
        <span className="muted small">{t("compare.totalDetail", { time: mins(total.totalSec) })}</span>
      </div>
      <p className="muted small">{t("compare.note")}</p>
      <div className="confirm-row">
        <button className="primary" onClick={openConfirm} disabled={!ready}>
          {t("compare.confirm")}
        </button>
        {disabled && <span className="muted small">{t("compare.alreadyRunning")}</span>}
      </div>

      <dialog ref={dialogRef} className="launch-dialog" aria-labelledby="compare-dialog-title">
        <h2 id="compare-dialog-title">{t("compare.dialogTitle")}</h2>
        <div className="launch-total">
          <span>{t("compare.total")}</span>
          <strong>{total.totalUsd == null ? t("region.unknown") : money(total.totalUsd)}</strong>
          <span className="muted small">{t("compare.totalDetail", { time: mins(total.totalSec) })}</span>
        </div>
        <p>{t("compare.dialogLead", { count: configs.length, model: estimate?.model.name ?? model, region })}</p>
        <ol className="small">
          {configs.map((c, i) => (
            <li key={i}>
              <code>{c.instance_type}</code> · {t(`region.storageNames.${c.model_storage}`)} · {t(`progress.purchase.${c.purchase_option}`)} ·{" "}
              {t("region.perHour", { price: money(configHourly(c, plans[c.instance_type], networkMode)) })}
            </li>
          ))}
        </ol>
        <p className="small">{t("compare.dialogSteps", { idle: idleStopMinutes, hours: maxLifetimeHours })}</p>
        <p className="warning small">{t("launch.disclaimer")}</p>
        {error && <p className="error">{error}</p>}
        <div className="dialog-buttons">
          <button className="secondary" onClick={() => dialogRef.current?.close()} disabled={submitting}>
            {t("launch.no")}
          </button>
          <button className="primary" onClick={yes} disabled={submitting || !ready}>
            {submitting ? t("launch.starting") : t("compare.yes")}
          </button>
        </div>
      </dialog>
    </section>
  );
}

function ComparisonResult({ comparison, onChange }: { comparison: Comparison; onChange: (c: Comparison) => void }) {
  const { t } = useTranslation();
  const { n, money, mins } = useFormatters();
  const [error, setError] = useState<string | null>(null);
  const results = comparison.configs.map((c) => c.result);
  const best = {
    ttft: bestValue(results.map((r) => r?.ttft_sec), "lower"),
    tps: bestValue(results.map((r) => r?.output_tokens_per_sec), "higher"),
    cost: bestValue(results.map((r) => r?.usd_per_million_tokens), "lower"),
    startup: bestValue(comparison.configs.map((c) => c.startup_sec), "lower"),
  };
  const mark = (v: number | null | undefined, b: number | null) => (v != null && v === b ? "best" : undefined);

  async function cancel() {
    setError(null);
    try {
      onChange(await cancelComparison(comparison.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <>
      <p className="small">
        {t("compare.summary", {
          model: comparison.model,
          region: comparison.region,
          context: comparison.context_length.toLocaleString(),
          kv: comparison.kv_cache_type,
          network: t(`region.networkNames.${comparison.network_mode}`),
          runs: comparison.runs,
        })}{" "}
        <span className={`badge ${comparison.status === "running" ? "running" : comparison.status === "succeeded" ? "succeeded" : comparison.status === "canceled" ? "neutral" : "failed"}`}>
          {t(`compare.status.${comparison.status}`)}
        </span>
      </p>
      {comparison.error && <p className="error">{comparison.error}</p>}
      {error && <p className="error">{error}</p>}
      <div className="environment-actions">
        {comparison.status === "running" && (
          <button className="secondary" onClick={cancel} disabled={comparison.cancel_requested}>
            {comparison.cancel_requested ? t("compare.canceling") : t("compare.cancel")}
          </button>
        )}
        <a className="chip" href={comparisonCsvUrl(comparison.id)} download={`comparison-${comparison.id}.csv`}>
          {t("benchmark.downloadCsv")}
        </a>
      </div>
      <div className="table-wrap">
        <table className="model-table comparison-table">
          <thead>
            <tr>
              <th>#</th>
              <th>{t("compare.columns.config")}</th>
              <th>{t("compare.columns.state")}</th>
              <th>{t("compare.columns.gpu")}</th>
              <th className="num">{t("compare.columns.startup")}</th>
              <th className="num">{t("benchmark.columns.ttft")}</th>
              <th className="num">{t("benchmark.columns.tokensPerSec")}</th>
              <th className="num">{t("benchmark.columns.vramMeasured")}</th>
              <th className="num">{t("compare.columns.hourly")}</th>
              <th className="num">{t("benchmark.columns.costPerMillion")}</th>
              <th className="num">{t("compare.columns.runCost")}</th>
            </tr>
          </thead>
          <tbody>
            {comparison.configs.map((c) => (
              <tr key={c.index}>
                <td>{c.index}</td>
                <td>
                  <code>{c.instance_type}</code>
                  <div className="muted small">
                    {t(`region.storageNames.${c.model_storage}`)} · {t(`progress.purchase.${c.purchase_option}`)}
                  </div>
                </td>
                <td>
                  <span className={isActive(c.status) ? "badge running" : c.status === "done" ? "badge succeeded" : c.status === "failed" ? "badge failed" : "badge neutral"}>
                    {t(`compare.configStatus.${c.status}`)}
                  </span>
                  {c.status === "starting" && c.stage && <div className="muted small">{t(`progress.stages.${c.stage}`)}</div>}
                  {c.error && <div className="error small">{c.error}</div>}
                  {c.destroyed === false && <div className="error small">{t("compare.notDestroyed", { workspace: c.workspace })}</div>}
                </td>
                <td>{c.result?.gpu ?? "—"}</td>
                <td className={`num ${mark(c.startup_sec, best.startup) ?? ""}`}>{mins(c.startup_sec)}</td>
                <td className={`num ${mark(c.result?.ttft_sec, best.ttft) ?? ""}`}>{n(c.result?.ttft_sec)}</td>
                <td className={`num ${mark(c.result?.output_tokens_per_sec, best.tps) ?? ""}`}>{n(c.result?.output_tokens_per_sec)}</td>
                <td className="num">{n(c.result?.vram_measured_gib)}</td>
                <td className="num">{c.result ? money(c.result.hourly_usd) : "—"}</td>
                <td className={`num ${mark(c.result?.usd_per_million_tokens, best.cost) ?? ""}`}>{c.result ? money(c.result.usd_per_million_tokens) : "—"}</td>
                <td className="num">{c.run_cost_usd == null ? "—" : money(c.run_cost_usd)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">{t("compare.resultNote")}</p>
    </>
  );
}
