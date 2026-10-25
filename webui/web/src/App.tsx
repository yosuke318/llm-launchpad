import { useState } from "react";
import { useTranslation } from "react-i18next";

import ModelPicker from "./components/ModelPicker";
import TerraformPanel from "./components/TerraformPanel";
import { LANGUAGES, saveLanguage, type Language } from "./i18n";

const TABS = ["models", "terraform"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<Tab>("models");

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

      <nav className="tabs" role="tablist">
        {TABS.map((name) => (
          <button key={name} role="tab" aria-selected={tab === name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>
            {t(`tabs.${name}`)}
          </button>
        ))}
      </nav>

      {/* 切り替えても実行中の terraform の出力を失わないよう、隠すだけにする */}
      <div hidden={tab !== "models"}>
        <ModelPicker />
      </div>
      <div hidden={tab !== "terraform"}>
        <TerraformPanel />
      </div>
    </div>
  );
}
