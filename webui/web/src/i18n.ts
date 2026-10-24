import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import ja from "./locales/ja.json";

export const LANGUAGES = ["ja", "en"] as const;
export type Language = (typeof LANGUAGES)[number];

const STORAGE_KEY = "llm-launchpad.language";

// 選んだ言語はブラウザに覚えておく。読めない(プライベートモードなど)ときは日本語にする
function savedLanguage(): Language {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (value === "ja" || value === "en") return value;
  } catch {
    // 読めなくても既定の言語で表示する
  }
  return "ja";
}

export function saveLanguage(language: Language) {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // 覚えられなくても、今の画面の言語は切り替わる
  }
}

void i18n.use(initReactI18next).init({
  resources: { ja: { translation: ja }, en: { translation: en } },
  lng: savedLanguage(),
  fallbackLng: "ja",
  interpolation: { escapeValue: false },
});

export default i18n;
