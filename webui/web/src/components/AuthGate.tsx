import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, UNAUTHORIZED_EVENT, getAuthStatus, login, logout, type AuthStatus } from "../api";

// サーバーで合言葉(LAUNCHPAD_ACCESS_TOKEN)を決めたときに、ログインの画面を出す。決めていなければ何もしない
export default function AuthGate({ children }: { children: (status: AuthStatus, onLogout: () => void) => ReactNode }) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    getAuthStatus()
      .then(setStatus)
      .catch((e: Error) => setLoadError(e.message));
    // どこかの読み込みで 401 が返ったら(合言葉が変わったなど)、ログインの画面に戻す
    const onUnauthorized = () => setStatus((s) => (s ? { ...s, authenticated: false } : s));
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  async function signOut() {
    try {
      await logout();
    } finally {
      setStatus((s) => (s ? { ...s, authenticated: false } : s));
    }
  }

  if (loadError) {
    return (
      <div className="page">
        <section className="card login">
          <h1>{t("app.title")}</h1>
          <p className="error">{loadError}</p>
        </section>
      </div>
    );
  }
  if (!status) return null;
  if (!status.authenticated) return <LoginForm onDone={() => setStatus({ required: true, authenticated: true })} />;
  return <>{children(status, signOut)}</>;
}

function LoginForm({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const [token, setToken] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSending(true);
    setError(null);
    try {
      await login(token);
      onDone();
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="page">
      <form className="card login" onSubmit={submit}>
        <h1>{t("app.title")}</h1>
        <p className="muted small">{t("auth.lead")}</p>
        <label className="field">
          <span>{t("auth.token")}</span>
          <input type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} autoFocus />
        </label>
        {error && <p className="error small">{error}</p>}
        <button className="primary" type="submit" disabled={sending || !token}>
          {sending ? t("auth.loggingIn") : t("auth.login")}
        </button>
      </form>
    </div>
  );
}
