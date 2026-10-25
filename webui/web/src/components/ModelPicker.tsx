import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { getCatalog, listModels, type Catalog, type ModelEstimate } from "../api";
import { isTight, vramSegments } from "../vram";

// 提供元(会社)を選び、モデルの一覧から選ぶ。コンテキスト長と KV キャッシュの型で、必要 VRAM と推奨のインスタンス型が変わる
export default function ModelPicker() {
  const { t, i18n } = useTranslation();
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [vendor, setVendor] = useState<string>("");
  const [engine, setEngine] = useState("ollama");
  const [contextLength, setContextLength] = useState(8192);
  const [kvCacheType, setKvCacheType] = useState("f16");
  const [models, setModels] = useState<ModelEstimate[]>([]);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const num = useMemo(() => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }), [i18n.language]);

  useEffect(() => {
    getCatalog()
      .then((c) => {
        setCatalog(c);
        setContextLength(c.defaults.context_length);
        setKvCacheType(c.defaults.kv_cache_type);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!catalog) return;
    let cancelled = false;
    setLoading(true);
    listModels({ vendor: vendor || undefined, context_length: contextLength, kv_cache_type: kvCacheType })
      .then((list) => {
        if (cancelled) return;
        setModels(list);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [catalog, vendor, contextLength, kvCacheType]);

  // 提供元を変えて選んでいたモデルが一覧から消えたら、選択を外す
  const selected = models.find((m) => m.model.tag === selectedTag) ?? null;

  if (!catalog) {
    return <section className="card">{error ? <p className="error">{t("models.loadFailed", { error })}</p> : <p className="muted">{t("models.loading")}</p>}</section>;
  }

  return (
    <div className="models">
      <section className="card">
        <div className="filters">
          <label className="field">
            <span>{t("models.engine")}</span>
            <select value={engine} onChange={(e) => setEngine(e.target.value)}>
              {catalog.engines.map((en) => (
                <option key={en.id} value={en.id} disabled={!en.available}>
                  {t(`models.engines.${en.id}`)}
                  {!en.available && ` (${t("models.comingLater")})`}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("models.contextLength")}</span>
            <select value={contextLength} onChange={(e) => setContextLength(Number(e.target.value))}>
              {catalog.context_lengths.map((c) => (
                <option key={c} value={c}>
                  {num.format(c)} {t("models.tokens")}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("models.kvCacheType")}</span>
            <select value={kvCacheType} onChange={(e) => setKvCacheType(e.target.value)}>
              {catalog.kv_cache_types.map((k) => (
                <option key={k} value={k}>
                  {k}
                  {k !== "f16" && ` (${t("models.kvQuantized")})`}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="vendors" role="radiogroup" aria-label={t("models.vendor")}>
          {["", ...catalog.vendors].map((v) => (
            <button key={v || "all"} role="radio" aria-checked={vendor === v} className={vendor === v ? "chip active" : "chip"} onClick={() => setVendor(v)}>
              {v || t("models.allVendors")}
            </button>
          ))}
        </div>

        {error && <p className="error">{t("models.loadFailed", { error })}</p>}

        <div className="table-wrap" aria-busy={loading}>
          <table className="model-table">
            <thead>
              <tr>
                <th>{t("models.columns.model")}</th>
                <th className="num">{t("models.columns.params")}</th>
                <th className="num">{t("models.columns.storage")}</th>
                <th className="num">{t("models.columns.vram")}</th>
                <th>{t("models.columns.instance")}</th>
                <th>{t("models.columns.license")}</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => {
                const e = m.vram_estimate;
                const isSelected = m.model.tag === selectedTag;
                return (
                  <tr key={m.model.tag} className={isSelected ? "selected" : ""} onClick={() => setSelectedTag(m.model.tag)}>
                    <td>
                      <button className="link" onClick={() => setSelectedTag(m.model.tag)} aria-pressed={isSelected}>
                        {m.model.name}
                      </button>
                      <div className="muted small">
                        <code>{m.model.tag}</code> · {m.model.vendor}
                      </div>
                    </td>
                    <td className="num">{num.format(m.model.params_b)}B</td>
                    <td className="num">{num.format(m.model.file_size_gb)} GB</td>
                    <td className="num">
                      {num.format(e.required_gib)} GiB
                      {m.context_capped && <div className="muted small">{t("models.cappedAt", { context: num.format(e.context_length) })}</div>}
                    </td>
                    <td>
                      {m.instance ? (
                        <>
                          <code>{m.instance_type}</code>
                          <div className="muted small">
                            {m.instance.gpu_model} × {m.instance.gpu_count}
                            {isTight(e) && <span className="warning"> · {t("models.tight")}</span>}
                          </div>
                        </>
                      ) : (
                        <span className="error">{t("models.noFit")}</span>
                      )}
                    </td>
                    <td className="small">{m.model.license}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card detail" aria-live="polite">
        {selected ? <ModelDetail estimate={selected} format={num.format} /> : <p className="muted">{t("models.selectPrompt")}</p>}
      </section>
    </div>
  );
}

function ModelDetail({ estimate, format }: { estimate: ModelEstimate; format: (n: number) => string }) {
  const { t } = useTranslation();
  const { model, vram_estimate: e, instance } = estimate;
  const seg = vramSegments(e);

  return (
    <>
      <div>
        <h2>{model.name}</h2>
        <p className="muted">
          <code>{model.tag}</code> · {model.vendor} · {model.license}
        </p>
      </div>

      <div className="recommend">
        <span className="muted small">{t("models.recommended")}</span>
        {instance ? (
          <>
            <strong>
              <code>{instance.type}</code>
            </strong>
            <span className="muted small">
              {t("models.instanceSpec", {
                gpu: instance.gpu_model,
                count: instance.gpu_count,
                vram: format(e.instance_vram_gib),
                vcpu: instance.vcpu,
                memory: instance.memory_gib,
              })}
            </span>
          </>
        ) : (
          <strong className="error">{t("models.noFit")}</strong>
        )}
      </div>

      <div>
        <div className="vram-bar" role="img" aria-label={t("models.vramBarLabel", { required: format(e.required_gib), total: format(e.instance_vram_gib) })}>
          <span className="seg weights" style={{ width: `${seg.weights}%` }} />
          <span className="seg kv" style={{ width: `${seg.kvCache}%` }} />
          <span className="seg overhead" style={{ width: `${seg.overhead}%` }} />
          <span className="seg free" style={{ width: `${seg.free}%` }} />
        </div>
        <dl className="breakdown">
          <div>
            <dt>
              <i className="dot weights" />
              {t("models.breakdown.weights")}
            </dt>
            <dd>{format(e.weights_gib)} GiB</dd>
          </div>
          <div>
            <dt>
              <i className="dot kv" />
              {t("models.breakdown.kvCache", { context: format(e.context_length), type: e.kv_cache_type })}
            </dt>
            <dd>{format(e.kv_cache_gib)} GiB</dd>
          </div>
          <div>
            <dt>
              <i className="dot overhead" />
              {t("models.breakdown.overhead")}
            </dt>
            <dd>{format(e.overhead_gib)} GiB</dd>
          </div>
          <div className="total">
            <dt>{t("models.breakdown.required")}</dt>
            <dd>{format(e.required_gib)} GiB</dd>
          </div>
          {instance && (
            <div>
              <dt>
                <i className="dot free" />
                {t("models.breakdown.headroom")}
              </dt>
              <dd className={isTight(e) ? "warning" : ""}>{format(e.headroom_gib)} GiB</dd>
            </div>
          )}
        </dl>
        {isTight(e) && <p className="warning small">{t("models.tightHelp")}</p>}
        {estimate.context_capped && <p className="muted small">{t("models.cappedHelp", { max: format(model.max_context) })}</p>}
      </div>

      <div>
        <h3>{t("models.fittingTypes")}</h3>
        <p className="types">
          {estimate.fitting_instance_types.length
            ? estimate.fitting_instance_types.map((type) => <code key={type}>{type}</code>)
            : t("models.noFit")}
        </p>
      </div>

      <dl className="specs">
        <div>
          <dt>{t("models.specs.params")}</dt>
          <dd>{format(model.params_b)}B</dd>
        </div>
        <div>
          <dt>{t("models.specs.quantization")}</dt>
          <dd>{model.quantization}</dd>
        </div>
        <div>
          <dt>{t("models.specs.storage")}</dt>
          <dd>{format(model.file_size_gb)} GB</dd>
        </div>
        <div>
          <dt>{t("models.specs.maxContext")}</dt>
          <dd>{format(model.max_context)}</dd>
        </div>
        <div>
          <dt>{t("models.specs.layers")}</dt>
          <dd>
            {model.attention_layers === model.layers
              ? format(model.layers)
              : t("models.specs.hybridLayers", { layers: model.layers, attention: model.attention_layers })}
          </dd>
        </div>
        <div>
          <dt>{t("models.specs.kvHeads")}</dt>
          <dd>
            {model.kv_heads} × {model.head_dim}
          </dd>
        </div>
      </dl>
      <p className="muted small">{t("models.estimateNote")}</p>
    </>
  );
}
