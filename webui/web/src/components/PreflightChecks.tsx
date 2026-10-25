import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { getPreflight, type Preflight, type PreflightCheck } from "../api";

interface Props {
  region: string;
  instanceType: string;
  // 結果が出るたびに呼ぶ。起動の確認(ポップアップ)に進めるかを、親が判断するのに使う
  onResult?: (result: Preflight | null) => void;
}

const ICON = { ok: "✓", ng: "✕", unknown: "?" } as const;

// 起動前の事前チェック。通らないものがあれば、理由と対処を出す
export default function PreflightChecks({ region, instanceType, onResult }: Props) {
  const { t } = useTranslation();
  const [result, setResult] = useState<Preflight | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);

  const recheck = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getPreflight({ region, instance_type: instanceType })
      .then((r) => {
        if (cancelled) return;
        setResult(r);
        setError(null);
        onResult?.(r);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        onResult?.(null);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [region, instanceType, nonce, onResult]);

  return (
    <div className="preflight" aria-busy={loading}>
      <div className="preflight-header">
        <h3>{t("preflight.heading")}</h3>
        <button className="chip" onClick={recheck} disabled={loading}>
          {t("preflight.recheck")}
        </button>
      </div>
      {error && <p className="error">{t("preflight.loadFailed", { error })}</p>}
      {result && (
        <>
          <ul className="checks">
            {result.checks.map((c) => (
              <li key={c.id} className={`check ${c.status}`}>
                <span className="check-icon" aria-hidden="true">
                  {ICON[c.status]}
                </span>
                <div>
                  <strong>{t(`preflight.names.${c.id}`)}</strong>{" "}
                  <span className="sr-only">{t(`preflight.status.${c.status}`)}</span>
                  <CheckDetail check={c} region={result.region} instanceType={result.instance_type} otherOptionOk={otherOptionOk(result, c)} />
                </div>
              </li>
            ))}
          </ul>
          <p className={result.can_proceed.spot || result.can_proceed.on_demand ? "ok-text" : "error"}>
            {summary(result, t)}
          </p>
        </>
      )}
    </div>
  );
}

function summary(r: Preflight, t: (key: string) => string) {
  const { spot, on_demand } = r.can_proceed;
  if (spot && on_demand) return t("preflight.summary.both");
  if (spot) return t("preflight.summary.spotOnly");
  if (on_demand) return t("preflight.summary.onDemandOnly");
  return t("preflight.summary.none");
}

// クォータのチェックで、もう片方の購入方法(スポット / オンデマンド)なら起動できるか
function otherOptionOk(r: Preflight, c: PreflightCheck) {
  if (c.id === "quota_spot") return r.can_proceed.on_demand;
  if (c.id === "quota_on_demand") return r.can_proceed.spot;
  return false;
}

function CheckDetail({
  check: c,
  region,
  instanceType,
  otherOptionOk,
}: {
  check: PreflightCheck;
  region: string;
  instanceType: string;
  otherOptionOk: boolean;
}) {
  const { t } = useTranslation();

  if (c.status === "unknown") {
    return <p className="muted small">{t("preflight.unknown", { error: c.error })}</p>;
  }

  switch (c.id) {
    case "credentials":
      return c.status === "ok" ? (
        <p className="muted small">{t("preflight.credentialsOk", { account: c.account, profile: c.profile ?? t("preflight.defaultProfile"), arn: c.arn })}</p>
      ) : (
        <p className="small">
          <span className="error">{c.error}</span>
          <br />
          {t("preflight.credentialsFix")}
        </p>
      );
    case "instance_type":
      return c.status === "ok" ? (
        <p className="muted small">{t("preflight.typeOk", { type: instanceType, zones: (c.availability_zones ?? []).join(", ") })}</p>
      ) : (
        <p className="small">
          <span className="error">{t("preflight.typeNg", { type: instanceType, region })}</span>
          <br />
          {t("preflight.typeFix")}
        </p>
      );
    case "quota_on_demand":
    case "quota_spot": {
      const numbers = t("preflight.quotaNumbers", { limit: c.limit_vcpus, used: c.used_vcpus, needed: c.needed_vcpus });
      return (
        <div className="small">
          <p className={c.status === "ok" ? "muted" : "error"}>
            {c.status === "ok" ? t("preflight.quotaOk", { numbers }) : t("preflight.quotaNg", { numbers, name: c.quota_name })}
          </p>
          {c.pending_requests?.map((p) => (
            <p key={p.created} className="muted">
              {t("preflight.pendingRequest", { value: p.desired_value, status: p.status, created: p.created.slice(0, 10) })}
            </p>
          ))}
          {c.status === "ng" && (
            <p>
              {t(c.pending_requests?.length ? "preflight.quotaFixPending" : "preflight.quotaFix")}
              {otherOptionOk && ` ${t("preflight.quotaUseOther")}`}{" "}
              <a href={c.request_url} target="_blank" rel="noreferrer">
                {t("preflight.quotaLink")}
              </a>
            </p>
          )}
        </div>
      );
    }
  }
}
