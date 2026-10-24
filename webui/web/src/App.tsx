import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, COMMANDS, listWorkspaces, startRun, type Command, type Workspace } from "./api";
import { LANGUAGES, saveLanguage, type Language } from "./i18n";
import { useRunStream } from "./runStream";

export default function App() {
  const { t, i18n } = useTranslation();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState("default");
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const [autoScroll, setAutoScroll] = useState(true);
  const { view, start } = useRunStream();
  const logRef = useRef<HTMLPreElement>(null);
  const running = view.status === "running";

  useEffect(() => {
    listWorkspaces()
      .then((list) => {
        setWorkspaces(list);
        const current = list.find((w) => w.current);
        if (current) setWorkspace(current.name);
      })
      .catch((e: Error) => setWorkspaceError(e.message));
  }, []);

  useEffect(() => {
    if (autoScroll && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [view.lines.length, autoScroll]);

  async function run(command: Command) {
    setStartError(null);
    try {
      start(await startRun(command, workspace));
    } catch (e) {
      const message = e instanceof ApiError && e.status === 409 ? t("terraform.busy") : (e as Error).message;
      setStartError(t("terraform.startFailed", { error: message }));
    }
  }

  function changeLanguage(language: Language) {
    void i18n.changeLanguage(language);
    saveLanguage(language);
    document.documentElement.lang = language;
  }

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>{t("app.title")}</h1>
          <p className="muted">{t("app.subtitle")}</p>
        </div>
        <label className="field inline">
          <span>{t("language.label")}</span>
          <select value={i18n.resolvedLanguage} onChange={(e) => changeLanguage(e.target.value as Language)}>
            {LANGUAGES.map((lang) => (
              <option key={lang} value={lang}>
                {t(`language.${lang}`)}
              </option>
            ))}
          </select>
        </label>
      </header>

      <main className="layout">
        <section className="card">
          <h2>{t("terraform.heading")}</h2>
          <p className="muted">{t("terraform.description")}</p>

          <label className="field">
            <span>{t("terraform.workspace")}</span>
            <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} disabled={running}>
              {(workspaces.length ? workspaces : [{ name: "default", current: true }]).map((w) => (
                <option key={w.name} value={w.name}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
          {workspaceError && <p className="error">{t("terraform.workspaceLoadFailed", { error: workspaceError })}</p>}

          <div className="commands">
            {COMMANDS.map((command) => (
              <button key={command} onClick={() => run(command)} disabled={running} title={t(`terraform.commandHelp.${command}`)}>
                <code>terraform {t(`terraform.commands.${command}`)}</code>
                <small>{t(`terraform.commandHelp.${command}`)}</small>
              </button>
            ))}
          </div>
          <p className="muted small">{t("terraform.planNeedsVars")}</p>
          {startError && <p className="error">{startError}</p>}
        </section>

        <section className="card output">
          <div className="output-header">
            <h2>{t("run.heading")}</h2>
            {view.run && (
              <div className="run-meta">
                <code>
                  terraform {view.run.args.join(" ")} ({view.run.workspace})
                </code>
                {view.status && <span className={`badge ${view.status}`}>{t(`run.status.${view.status}`)}</span>}
                {view.exitCode !== null && <span className="muted">{t("run.exitCode", { code: view.exitCode })}</span>}
                <span className="muted">{t("run.lines", { count: view.lines.length })}</span>
              </div>
            )}
          </div>
          {view.reconnecting && <p className="warning">{t("run.reconnecting")}</p>}
          <pre ref={logRef} className="log" aria-live="polite">
            {view.run ? view.lines.join("\n") : t("run.empty")}
          </pre>
          <label className="field inline small">
            <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
            <span>{t("run.autoScroll")}</span>
          </label>
        </section>
      </main>
    </div>
  );
}
