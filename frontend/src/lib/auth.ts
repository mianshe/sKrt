import { useSyncExternalStore } from "react";
import { API_BASE } from "../config/apiBase";

const ACCESS_TOKEN_KEY = "xm_access_token";
const AUTH_CHANGE_EVENT = "xm-auth-change";

function readAccessToken(): string {
  try {
    return localStorage.getItem(ACCESS_TOKEN_KEY)?.trim() || "";
  } catch {
    return "";
  }
}

function emitAuthChange(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(AUTH_CHANGE_EVENT));
}

function subscribeAuth(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;

  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === ACCESS_TOKEN_KEY) {
      listener();
    }
  };
  const onAuthChange = () => listener();

  window.addEventListener("storage", onStorage);
  window.addEventListener(AUTH_CHANGE_EVENT, onAuthChange);

  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(AUTH_CHANGE_EVENT, onAuthChange);
  };
}

export function getAccessToken(): string {
  return readAccessToken();
}

export function setAccessToken(token: string | null): void {
  try {
    if (token && token.trim()) localStorage.setItem(ACCESS_TOKEN_KEY, token.trim());
    else localStorage.removeItem(ACCESS_TOKEN_KEY);
  } catch {
    // ignore local storage failures
  }
  emitAuthChange();
}

export function useAccessToken(): string {
  return useSyncExternalStore(subscribeAuth, readAccessToken, () => "");
}

export async function verifyLocalAuthSession(token: string): Promise<boolean> {
  const normalized = token.trim();
  if (!normalized) return false;
  try {
    const response = await fetch(`${API_BASE}/auth/me`, {
      headers: {
        Authorization: `Bearer ${normalized}`,
      },
    });
    return response.ok;
  } catch {
    return false;
  }
}
