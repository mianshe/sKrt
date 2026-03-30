declare const __API_BASE__: string | undefined;

export const API_BASE: string =
  (typeof __API_BASE__ === "string" && __API_BASE__.trim()) ||
  (import.meta.env.VITE_API_BASE?.trim() || "") ||
  "/api";

