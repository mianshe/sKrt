import { useEffect, useState } from "react";

export type EmbeddingMode = "auto" | "local" | "api";

const EMBEDDING_MODE_KEY = "xm_embedding_mode";

export function normalizeEmbeddingMode(value: unknown): EmbeddingMode {
  const mode = String(value || "").trim().toLowerCase();
  if (mode === "local" || mode === "api") return mode;
  return "auto";
}

export function getEmbeddingModePreference(): EmbeddingMode {
  try {
    return normalizeEmbeddingMode(localStorage.getItem(EMBEDDING_MODE_KEY));
  } catch {
    return "auto";
  }
}

export function setEmbeddingModePreference(mode: EmbeddingMode): void {
  try {
    localStorage.setItem(EMBEDDING_MODE_KEY, normalizeEmbeddingMode(mode));
  } catch {
    // ignore
  }
}

export function useEmbeddingModePreference() {
  const [embeddingMode, setEmbeddingMode] = useState<EmbeddingMode>(getEmbeddingModePreference);

  useEffect(() => {
    setEmbeddingModePreference(embeddingMode);
  }, [embeddingMode]);

  return [embeddingMode, setEmbeddingMode] as const;
}
