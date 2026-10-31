import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { compareRegions, type RegionComparison } from "../api";
import { placementRisk } from "../launch";

interface Props {
  instanceType: string;
  region: string;
  onChoose: (region: string) => void;
}

// リージョンごとに、インスタンス型の料金とスポットの起動しやすさを並べ、安くて中断されにくいリージョンをすすめる。
// 全部のリージョンに問い合わせるので、開いたときだけ読む
export default function RegionCompare({ instanceType, region, onChoose }: Props) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<RegionComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const usd = useMemo(
    () => new Intl.NumberFormat(i18n.language, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 4 }),
    [i18n.language],
  );
  const money = (v: number | null | undefined) => (v == null ? "—" : t("region.perHour", { price: usd.format(v) }));

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    // 今のリージョンは画面で印を付けるので、リージョンを変えても読み直さない
    compareRegions({ instance_type: instanceType, region })
      .then((r) => !cancelled && setResult(r))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, instanceType]);

  const rows = result?.instance_type === instanceType ? result.regions : [];
  const name = (code: string | null) => rows.find((r) => r.code === code)?.name ?? code;
  const spotRow = rows.find((r) => r.code === result?.recommended.spot);
  const onDemandRow = rows.find((r) => r.code === result?.recommended.on_demand);

  return (
    <details className="region-compare" onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary>{t("regionCompare.open", { type: instanceType })}</summary>
      {loading && <p className="muted small">{t("regionCompare.loading")}</p>}
      {error && <p className="error small">{t("region.loadFailed", { error })}</p>}
      {result && rows.length > 0 && (
        <>
          <ul className="region-recommend small">
            <li>
              {spotRow?.spot_min
                ? t("regionCompare.recommendSpot", {
                    region: name(spotRow.code),
                    code: spotRow.code,
                    price: money(spotRow.spot_min.hourly_usd),
                    score: spotRow.placement_score,
                  })
                : t("regionCompare.noLowRiskSpot", { score: result.low_risk_score })}
            </li>
            {onDemandRow && (
              <li>
                {t("regionCompare.recommendOnDemand", { region: name(onDemandRow.code), code: onDemandRow.code, price: money(onDemandRow.on_demand_hourly_usd) })}
              </li>
            )}
          </ul>
          <div className="table-wrap">
            <table className="model-table">
              <thead>
                <tr>
                  <th>{t("region.region")}</th>
                  <th className="num">{t("region.spot")}</th>
                  <th>{t("regionCompare.score")}</th>
                  <th className="num">{t("region.onDemand")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const risk = r.placement_score == null ? null : placementRisk(r.placement_score);
                  const current = r.code === region;
                  return (
                    <tr key={r.code} className={current ? "selected" : ""}>
                      <td>
                        {r.name} <span className="muted small">({r.code})</span>
                        {(r.code === result.recommended.spot || r.code === result.recommended.on_demand) && (
                          <div className="region-badges">
                            {r.code === result.recommended.spot && <span className="badge succeeded">{t("regionCompare.badgeSpot")}</span>}
                            {r.code === result.recommended.on_demand && <span className="badge succeeded">{t("regionCompare.badgeOnDemand")}</span>}
                          </div>
                        )}
                      </td>
                      <td className="num">
                        {money(r.spot_min?.hourly_usd)}
                        {r.spot_min && <div className="muted small">{r.spot_min.availability_zone}</div>}
                      </td>
                      <td>{risk ? <span className={`risk ${risk}`}>{t(`regionCompare.risk.${risk}`, { score: r.placement_score })}</span> : "—"}</td>
                      <td className="num">{money(r.on_demand_hourly_usd)}</td>
                      <td>
                        {current ? (
                          <span className="muted small">{t("regionCompare.current")}</span>
                        ) : (
                          <button className="secondary" onClick={() => onChoose(r.code)}>
                            {t("regionCompare.choose")}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      {result && rows.length === 0 && <p className="muted small">{t("regionCompare.none", { type: instanceType })}</p>}
      {result?.warnings.map((w) => (
        <p key={w} className="warning small">
          {w}
        </p>
      ))}
      <p className="muted small">{t("regionCompare.note")}</p>
    </details>
  );
}
