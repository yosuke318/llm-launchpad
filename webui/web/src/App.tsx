import { useState } from "react";
import { useTranslation } from "react-i18next";

import EnvironmentsPanel from "./components/EnvironmentsPanel";
import ModelPicker from "./components/ModelPicker";
import ProgressPanel from "./components/ProgressPanel";
import TerraformPanel from "./components/TerraformPanel";
import { LANGUAGES, saveLanguage, type Language } from "./i18n";
import { RunProvider } from "./runContext";

const TABS = ["models", "progress", "environments", "terraform"] as const;
type Tab = (typeof TABS)[number];

// タブとパネルを ARIA で結びつける id(WAI-ARIA の tabs パターン)
const tabId = (name: Tab) => `tab-${name}`;
const panelId = (name: Tab) => `panel-${name}`;

export default function App() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<Tab>("models");

  function changeLanguage(language: Language) {
    void i18n.changeLanguage(language);
    saveLanguage(language);
    document.documentElement.lang = language;
  }

  return (
    <RunProvider showProgress={() => setTab("progress")} showTerraform={() => setTab("terraform")}>
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

        <nav className="tabs" role="tablist" aria-label={t("tabs.label")}>
          {TABS.map((name) => (
            <button
              key={name}
              id={tabId(name)}
              role="tab"
              aria-selected={tab === name}
              aria-controls={panelId(name)}
              className={tab === name ? "active" : ""}
              onClick={() => setTab(name)}
            >
              {t(`tabs.${name}`)}
            </button>
          ))}
        </nav>

        {/* 切り替えても実行中の terraform の出力を失わないよう、隠すだけにする */}
        <div id={panelId("models")} role="tabpanel" aria-labelledby={tabId("models")} hidden={tab !== "models"}>
          <ModelPicker />
        </div>
        <div id={panelId("progress")} role="tabpanel" aria-labelledby={tabId("progress")} hidden={tab !== "progress"}>
          <ProgressPanel />
        </div>
        <div id={panelId("environments")} role="tabpanel" aria-labelledby={tabId("environments")} hidden={tab !== "environments"}>
          {/* 一覧は読むたびに terraform state pull と AWS への問い合わせをするので、開いている間だけ動かす */}
          {tab === "environments" && <EnvironmentsPanel />}
        </div>
        <div id={panelId("terraform")} role="tabpanel" aria-labelledby={tabId("terraform")} hidden={tab !== "terraform"}>
          <TerraformPanel />
        </div>
      </div>
    </RunProvider>
  );
}
