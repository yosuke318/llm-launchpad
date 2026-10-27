import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { getRegionPlan, listRegions, type ModelEstimate, type Preflight, type Region, type RegionPlan, type StorageId } from "../api";
import { hourlyTotals, minutes } from "../cost";
import LaunchDialog from "./LaunchDialog";
import PreflightChecks from "./PreflightChecks";

interface Props {
  estimate: ModelEstimate;
  contextLength: number;
  kvCacheType: string;
}

// 選んだモデルについて、リージョンとモデルの置き場所(ストレージ方式)を選ぶ。料金と起動時間の目安を並べる
export default function RegionStoragePicker({ estimate, contextLength, kvCacheType }: Props) {
  const { t, i18n } = useTranslation();
  const [regions, setRegions] = useState<Region[]>([]);
  const [defaultRegion, setDefaultRegion] = useState("ap-northeast-1");
  const [region, setRegion] = useState("ap-northeast-1");
  const [storage, setStorage] = useState<StorageId>("s3_cache");
  const [plan, setPlan] = useState<RegionPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [confirming, setConfirming] = useState(false);
  const onPreflight = useCallback((r: Preflight | null) => setPreflight(r), []);

  const usd = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    [i18n.language],
  );
  // 時間あたりは小さい額なので小数4桁まで、月額は小数2桁で出す
  const usdMonthly = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    [i18n.language],
  );
  const money = (v: number | null | undefined) => (v == null ? t("region.unknown") : usd.format(v));
  const moneyMonthly = (v: number | null | undefined) => (v == null ? t("region.unknown") : usdMonthly.format(v));

  useEffect(() => {
    listRegions()
      .then((r) => {
        setRegions(r.regions);
        setDefaultRegion(r.default);
        setRegion(r.default);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  const tag = estimate.model.tag;
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getRegionPlan({ model: tag, region, context_length: contextLength, kv_cache_type: kvCacheType })
      .then((p) => {
        if (cancelled) return;
        setPlan(p);
        setError(null);
      })
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [tag, region, contextLength, kvCacheType]);

  const selected = plan?.storage_options.find((o) => o.id === storage) ?? null;
  const totals = plan && selected ? hourlyTotals(plan, selected) : null;
  const regionName = (code: string) => regions.find((r) => r.code === code)?.name ?? code;

  return (
    <section className="card region" aria-busy={loading}>
      <h2>{t("region.heading", { model: estimate.model.name })}</h2>

      <label className="field">
        <span>{t("region.region")}</span>
        <select value={region} onChange={(e) => setRegion(e.target.value)}>
          {(regions.length ? regions : [{ code: region, name: region }]).map((r) => (
            <option key={r.code} value={r.code}>
              {r.name} ({r.code})
            </option>
          ))}
        </select>
      </label>
      {region !== defaultRegion && <p className="warning small">{t("region.moveNotice", { region: regionName(region) })}</p>}
      {error && <p className="error">{t("region.loadFailed", { error })}</p>}

      {plan && (
        <>
          <div className="availability">
            {plan.availability.available === false ? (
              <p className="error">{t("region.notOffered", { type: plan.instance_type })}</p>
            ) : (
              <p>
                <code>{plan.instance_type}</code>{" "}
                {plan.availability.availability_zones
                  ? t("region.offeredIn", { zones: plan.availability.availability_zones.join(", ") })
                  : t("region.unknown")}
              </p>
            )}
            <dl className="prices">
              <div>
                <dt>{t("region.spot")}</dt>
                <dd>
                  {plan.prices.spot_min ? (
                    <>
                      {t("region.perHour", { price: money(plan.prices.spot_min.hourly_usd) })}
                      <span className="muted small"> ({plan.prices.spot_min.availability_zone})</span>
                    </>
                  ) : (
                    t("region.unknown")
                  )}
                </dd>
              </div>
              <div>
                <dt>{t("region.onDemand")}</dt>
                <dd>{t("region.perHour", { price: money(plan.prices.on_demand_hourly_usd) })}</dd>
              </div>
            </dl>
          </div>

          <fieldset className="storage-options">
            <legend>{t("region.storage")}</legend>
            {plan.storage_options.map((o) => (
              <label key={o.id} className={`storage-option ${storage === o.id ? "selected" : ""}`}>
                <input type="radio" name="storage" value={o.id} checked={storage === o.id} onChange={() => setStorage(o.id)} />
                <div>
                  <strong>{t(`region.storageNames.${o.id}`)}</strong>
                  <p className="muted small">{t(`region.storageHelp.${o.id}`)}</p>
                  <dl className="option-figures">
                    <div>
                      <dt>{t("region.storageMonthly")}</dt>
                      <dd>
                        {t("region.perMonth", { price: moneyMonthly(o.storage_monthly_usd) })}
                        {o.id === "ebs" && o.storage_throughput_monthly_usd != null && (
                          <span className="muted small">
                            {" "}
                            {t("region.ebsThroughput", { size: o.volume_gib, throughput: o.volume_throughput_mibps, price: moneyMonthly(o.storage_throughput_monthly_usd) })}
                          </span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>{t("region.startup")}</dt>
                      <dd>
                        {o.startup_cached_sec == null
                          ? t("region.minutes", { count: minutes(o.startup_first_sec) })
                          : t("region.startupFirstLater", { first: minutes(o.startup_first_sec), later: minutes(o.startup_cached_sec) })}
                      </dd>
                    </div>
                  </dl>
                  {!o.ready && <p className="warning small">{t(`region.prerequisite.${o.prerequisite}`, { region })}</p>}
                </div>
              </label>
            ))}
          </fieldset>

          {selected && totals && (
            <div className="hourly-total">
              <span className="muted small">{t("region.hourlyTotal")}</span>
              <strong>{t("region.perHour", { price: money(totals.spot) })}</strong>
              <span className="muted small">
                {t("region.hourlyTotalDetail", { onDemand: money(totals.onDemand), disk: selected.root_volume_gib })}
              </span>
            </div>
          )}

          {/* 起動前の事前チェック。通らないものがあれば、起動の確認に進めない */}
          <PreflightChecks region={region} instanceType={plan.instance_type} onResult={onPreflight} />

          {/* 事前チェックに通った購入方法が1つもなければ、起動の確認に進めない */}
          <div className="confirm-row">
            <button
              className="primary"
              onClick={() => setConfirming(true)}
              disabled={!selected || !preflight || !(preflight.can_proceed.spot || preflight.can_proceed.on_demand) || preflight.region !== region}
            >
              {t("region.confirm")}
            </button>
            {preflight && !(preflight.can_proceed.spot || preflight.can_proceed.on_demand) && (
              <span className="muted small">{t("region.confirmBlocked")}</span>
            )}
          </div>
          {confirming && selected && preflight && (
            <LaunchDialog estimate={estimate} plan={plan} option={selected} preflight={preflight} onClose={() => setConfirming(false)} />
          )}

          {plan.warnings.length > 0 && (
            <ul className="warning small">
              {plan.warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
          <p className="muted small">{t("region.note", { source: plan.measured.source })}</p>
        </>
      )}
    </section>
  );
}
