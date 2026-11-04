import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { listNotifications, type AppNotification, type NotificationList } from "../api";
import { mergeNotifications, newNotifications, notificationText } from "../notify";

// 画面がサーバーに知らせを聞きに行く間隔
const POLL_MS = 30_000;
// 画面に並べる件数
const SHOWN = 10;
// 画面が持っておく件数(サーバーが1回に返す件数と同じ)
const KEEP = 50;
const STORAGE_KEY = "llm-launchpad.desktopNotifications";

type Permission = NotificationPermission | "unsupported";

function permission(): Permission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

// デスクトップ通知を出すか。許可したあとでも、画面で止められる。読めなければ出す
function desktopEnabled() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

function saveDesktopEnabled(on: boolean) {
  try {
    window.localStorage.setItem(STORAGE_KEY, on ? "on" : "off");
  } catch {
    // 覚えられなくても、今の画面では切り替わる
  }
}

// 起動した環境の知らせ。準備完了・自動停止の直前・スポットの中断をデスクトップ通知で出し、最近のものを並べる
export default function NotificationCenter() {
  const { t, i18n } = useTranslation();
  const [data, setData] = useState<NotificationList | null>(null);
  const [items, setItems] = useState<AppNotification[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [perm, setPerm] = useState<Permission>(permission);
  const [enabled, setEnabled] = useState(desktopEnabled);
  // 最後に見た知らせの id。初めて読むまでは null
  const lastId = useRef<number | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function poll() {
      try {
        // 初めは新しいほうから、そのあとは最後に見た id の続きを読む。間に多く足されても取りこぼさない
        const res = await listNotifications(lastId.current ?? 0);
        if (cancelled) return;
        const fresh = newNotifications(res.notifications, lastId.current);
        lastId.current = Math.max(lastId.current ?? 0, ...res.notifications.map((n) => n.id));
        setData(res);
        setItems((prev) => mergeNotifications(prev, res.notifications, KEEP));
        setError(null);
        if (enabledRef.current && permission() === "granted") {
          for (const n of fresh) {
            const { key, params } = notificationText(n, new Date());
            const desktop = new Notification(t("notify.title"), { body: t(key, params), tag: n.key });
            desktop.onclick = () => window.focus();
          }
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
      if (!cancelled) timer = window.setTimeout(poll, POLL_MS);
    }

    void poll();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // 言語を変えても聞き直さない(文は表示するときに作る)
  }, []);

  async function enable() {
    if (perm === "default") {
      const result = await Notification.requestPermission();
      setPerm(result);
      if (result !== "granted") return;
    }
    setEnabled(true);
    saveDesktopEnabled(true);
  }

  function disable() {
    setEnabled(false);
    saveDesktopEnabled(false);
  }

  const recent = items.slice(-SHOWN).reverse();
  const time = new Intl.DateTimeFormat(i18n.language, { dateStyle: "short", timeStyle: "short" });

  return (
    <details className="notifications">
      <summary>{t("notify.open", { count: items.length })}</summary>
      <div className="notifications-body">
        <div className="notifications-controls">
          {perm === "unsupported" ? (
            <span className="muted small">{t("notify.unsupported")}</span>
          ) : perm === "denied" ? (
            <span className="warning small">{t("notify.denied")}</span>
          ) : perm === "granted" && enabled ? (
            <button className="secondary" onClick={disable}>
              {t("notify.on")}
            </button>
          ) : (
            <button onClick={() => void enable()}>{perm === "granted" ? t("notify.off") : t("notify.enable")}</button>
          )}
          {data && <span className="muted small">{data.slack ? t("notify.slackOn") : t("notify.slackOff")}</span>}
        </div>
        {data?.interval_sec === 0 && <p className="warning small">{t("notify.watchOff")}</p>}
        {data?.slack_error && <p className="error small">{data.slack_error}</p>}
        {error && <p className="error small">{t("notify.loadFailed", { error })}</p>}
        {recent.length ? (
          <ul className="notification-list">
            {recent.map((n) => {
              // 一覧では、知らせたときの残り分数を出す
              const { key, params } = notificationText(n, new Date(n.created_at));
              return (
                <li key={n.id}>
                  <span className="muted small">{time.format(new Date(n.created_at))}</span> {t(key, params)}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted small">{t("notify.empty")}</p>
        )}
        <p className="muted small">{t("notify.note")}</p>
      </div>
    </details>
  );
}
