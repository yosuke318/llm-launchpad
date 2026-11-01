import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { addModel } from "../api";

interface Props {
  // 追加したモデルのタグ(ollama pull に渡す名前)
  onAdded: (tag: string) => void;
}

// 一覧にないモデルを、Ollama のタグか Hugging Face のリポジトリ名で追加する。サイズや層の数は registry から読み取る
export default function AddModel({ onAdded }: Props) {
  const { t } = useTranslation();
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const added = await addModel(ref);
      setRef("");
      onAdded(added.tag);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="add-model">
      <summary>{t("addModel.open")}</summary>
      <form onSubmit={submit}>
        <input
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder={t("addModel.placeholder")}
          aria-label={t("addModel.label")}
          spellCheck={false}
          autoCapitalize="off"
        />
        <button type="submit" disabled={busy || !ref.trim()}>
          {busy ? t("addModel.looking") : t("addModel.submit")}
        </button>
      </form>
      {error && <p className="error small">{t("addModel.failed", { error })}</p>}
      <p className="muted small">{t("addModel.note")}</p>
    </details>
  );
}
