import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, launch, type ModelEstimate, type Preflight, type RegionPlan, type StorageOption } from "../api";
import { minutes } from "../cost";
import {
  defaultEnvId,
  ebsVolumeZone,
  isValidEnvId,
  launchHourly,
  launchVariables,
  validIdleStop,
  validMaxLifetime,
  zonePlacement,
} from "../launch";
import { useRun } from "../runContext";

interface Props {
  estimate: ModelEstimate;
  plan: RegionPlan;
  option: StorageOption;
  preflight: Preflight;
  onClose: () => void;
}

// 起動の確認。表示した内容を tfvars に書き、Yes で terraform apply に進み、起動の進み具合のタブに切り替える。No なら閉じて選び直す
export default function LaunchDialog({ estimate, plan, option, preflight, onClose }: Props) {
  const { t, i18n } = useTranslation();
  const { start, showProgress } = useRun();
  const dialogRef = useRef<HTMLDialogElement>(null);

  const [envId, setEnvId] = useState(() => defaultEnvId(estimate.model.tag));
  const [purchaseOption, setPurchaseOption] = useState<"spot" | "on-demand">(preflight.can_proceed.spot ? "spot" : "on-demand");
  const [idleStopMinutes, setIdleStopMinutes] = useState(30);
  const [maxLifetimeHours, setMaxLifetimeHours] = useState(8);
  // 在庫のないAZを避けたいときに選ぶ。空なら terraform が選ぶ
  const [availabilityZone, setAvailabilityZone] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);

  const usd = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    [i18n.language],
  );
  const num = useMemo(() => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }), [i18n.language]);
  const money = (v: number | null | undefined) => (v == null ? t("region.unknown") : usd.format(v));

  const zones = plan.availability.launchable_zones ?? [];
  // EBS永続はボリュームのあるAZに決まる
  const fixedZone = option.id === "ebs" ? ebsVolumeZone(plan) : null;
  // 価格と中断のリスクを出すAZ。自動なら terraform が選ぶAZ(選べるAZの最初)
  const zone = option.id === "ebs" ? fixedZone : (availabilityZone ?? zones[0] ?? null);
  const settings = { purchaseOption, idleStopMinutes, maxLifetimeHours, availabilityZone };
  const variables = launchVariables(estimate, plan, option, settings);
  const hourly = launchHourly(plan, option, purchaseOption, zone);
  const placement = zonePlacement(plan.spot_placement_scores, zone);
  const e = estimate.vram_estimate;
  const instance = estimate.instance;
  const canSpot = preflight.can_proceed.spot;
  const canOnDemand = preflight.can_proceed.on_demand;
  const allowed = purchaseOption === "spot" ? canSpot : canOnDemand;

  // スポットのときは、AZごとの価格と起動できる見込みも出す
  const zoneLabel = (z: string) => {
    const price = plan.prices.spot_hourly_usd_by_az[z];
    const score = plan.spot_placement_scores?.[z];
    if (purchaseOption !== "spot" || (price == null && score == null)) return z;
    return t("launch.zoneSpot", { zone: z, price: money(price), score: score ?? "?" });
  };

  const envIdOk = isValidEnvId(envId);
  const idleOk = validIdleStop(idleStopMinutes);
  const lifetimeOk = validMaxLifetime(maxLifetimeHours);
  const ready = allowed && envIdOk && idleOk && lifetimeOk && !submitting;

  async function yes() {
    setSubmitting(true);
    setError(null);
    try {
      const run = await launch(envId, variables);
      start(run);
      showProgress();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? t("terraform.busy") : (err as Error).message);
      setSubmitting(false);
    }
  }

  return (
    <dialog ref={dialogRef} className="launch-dialog" aria-labelledby="launch-title" onCancel={onClose}>
      <h2 id="launch-title">{t("launch.title")}</h2>
      <p className="muted small">{t("launch.lead")}</p>

      <div className="launch-total">
        <span className="muted small">{t(purchaseOption === "spot" ? "launch.hourlySpot" : "launch.hourlyOnDemand")}</span>
        <strong>{t("region.perHour", { price: money(hourly) })}</strong>
        {option.storage_monthly_usd != null && option.storage_monthly_usd > 0 && (
          <span className="muted small">{t("launch.storageWhileStopped", { price: money(option.storage_monthly_usd) })}</span>
        )}
      </div>

      <dl className="launch-summary">
        <div>
          <dt>{t("launch.model")}</dt>
          <dd>
            {estimate.model.name} <code>{estimate.model.tag}</code>
          </dd>
        </div>
        <div>
          <dt>{t("launch.license")}</dt>
          <dd>{estimate.model.license}</dd>
        </div>
        <div>
          <dt>{t("launch.instanceType")}</dt>
          <dd>
            <code>{plan.instance_type}</code>
            {instance && ` (${instance.gpu_model} × ${instance.gpu_count}, VRAM ${num.format(e.instance_vram_gib)} GiB)`}
          </dd>
        </div>
        <div>
          <dt>{t("launch.region")}</dt>
          <dd>{plan.region}</dd>
        </div>
        <div>
          <dt>{t("launch.storage")}</dt>
          <dd>
            {t(`region.storageNames.${option.id}`)} · {t("launch.startup", { count: minutes(option.startup_cached_sec ?? option.startup_first_sec) })}
          </dd>
        </div>
        <div>
          <dt>{t("launch.disk")}</dt>
          <dd>
            {t("launch.rootVolume", { size: option.root_volume_gib })}
            {option.id === "ebs" && option.volume_gib != null && ` + ${t("launch.modelVolume", { size: option.volume_gib })}`}
          </dd>
        </div>
        <div>
          <dt>{t("launch.context")}</dt>
          <dd>
            {num.format(e.context_length)} {t("models.tokens")} · {t("launch.quantization", { quantization: estimate.model.quantization, kv: e.kv_cache_type })}
          </dd>
        </div>
        <div>
          <dt>{t("launch.vram")}</dt>
          <dd>
            {t("launch.vramBreakdown", {
              required: num.format(e.required_gib),
              weights: num.format(e.weights_gib),
              kv: num.format(e.kv_cache_gib),
              overhead: num.format(e.overhead_gib),
              headroom: num.format(e.headroom_gib),
            })}
          </dd>
        </div>
      </dl>

      <fieldset className="launch-options">
        <legend>{t("launch.purchase")}</legend>
        <label className={!canSpot ? "disabled" : ""}>
          <input type="radio" name="purchase" checked={purchaseOption === "spot"} disabled={!canSpot} onChange={() => setPurchaseOption("spot")} />
          {t("launch.spot", { price: money(zone ? plan.prices.spot_hourly_usd_by_az[zone] : plan.prices.spot_min?.hourly_usd) })}
          {!canSpot && <span className="error small"> {t("launch.blockedByPreflight")}</span>}
        </label>
        {purchaseOption === "spot" && (
          <p className="small">
            {placement ? (
              <span className={`risk ${placement.risk}`}>
                {t(`launch.risk.${placement.risk}`, { score: placement.score, zone: placement.availabilityZone })}
              </span>
            ) : (
              <span className="muted">{t("launch.riskUnknown")}</span>
            )}{" "}
            <span className="muted">{t("launch.riskNote")}</span>
          </p>
        )}
        <label className={!canOnDemand ? "disabled" : ""}>
          <input type="radio" name="purchase" checked={purchaseOption === "on-demand"} disabled={!canOnDemand} onChange={() => setPurchaseOption("on-demand")} />
          {t("launch.onDemand", { price: money(plan.prices.on_demand_hourly_usd) })}
          {!canOnDemand && <span className="error small"> {t("launch.blockedByPreflight")}</span>}
        </label>
      </fieldset>

      <fieldset className="launch-options">
        <legend>{t("launch.zone")}</legend>
        {option.id === "ebs" ? (
          <p className="small">{t("launch.zoneFixedByVolume", { zone: fixedZone ?? "?" })}</p>
        ) : (
          <label className="field">
            <select value={availabilityZone ?? ""} onChange={(ev) => setAvailabilityZone(ev.target.value || null)}>
              <option value="">{zones.length ? t("launch.zoneAuto", { zone: zones[0] }) : t("launch.zoneAutoUnknown")}</option>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {zoneLabel(z)}
                </option>
              ))}
            </select>
            <span className="muted small">{t("launch.zoneHelp")}</span>
          </label>
        )}
      </fieldset>

      <fieldset className="launch-options">
        <legend>{t("launch.autoStop")}</legend>
        <label className="field">
          <span>{t("launch.idleStop")}</span>
          <input type="number" min={0} max={1440} value={idleStopMinutes} onChange={(ev) => setIdleStopMinutes(Number(ev.target.value))} />
          {!idleOk && <span className="error small">{t("launch.idleStopInvalid")}</span>}
        </label>
        <label className="field">
          <span>{t("launch.maxLifetime")}</span>
          <input type="number" min={0} max={720} value={maxLifetimeHours} onChange={(ev) => setMaxLifetimeHours(Number(ev.target.value))} />
          {!lifetimeOk && <span className="error small">{t("launch.maxLifetimeInvalid")}</span>}
        </label>
      </fieldset>

      <label className="field">
        <span>{t("launch.envId")}</span>
        <input type="text" value={envId} onChange={(ev) => setEnvId(ev.target.value.trim())} spellCheck={false} />
        <span className={envIdOk ? "muted small" : "error small"}>{envIdOk ? t("launch.envIdHelp") : t("launch.envIdInvalid")}</span>
      </label>

      <details className="small">
        <summary>{t("launch.tfvars", { path: `terraform/tfvars/${envId}.tfvars.json` })}</summary>
        <pre className="tfvars">{JSON.stringify(variables, null, 2)}</pre>
      </details>

      <p className="warning small">{t("launch.disclaimer")}</p>
      {error && <p className="error">{t("launch.failed", { error })}</p>}

      <div className="dialog-buttons">
        <button className="secondary" onClick={onClose} disabled={submitting}>
          {t("launch.no")}
        </button>
        <button className="primary" onClick={yes} disabled={!ready}>
          {submitting ? t("launch.starting") : t("launch.yes")}
        </button>
      </div>
    </dialog>
  );
}
