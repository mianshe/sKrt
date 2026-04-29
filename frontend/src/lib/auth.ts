import { useSyncExternalStore } from "react";
import { API_BASE } from "../config/apiBase";

const ACCESS_TOKEN_KEY = "xm_access_token";
const AUTH_CHANGE_EVENT = "xm-auth-change";
const AUTH_BOOTSTRAP_EVENT = "xm-auth-bootstrap";

export type AuthBootstrapStatus = "idle" | "checking" | "ready";

let authBootstrapStatus: AuthBootstrapStatus = "idle";
let authBootstrapPromise: Promise<void> | null = null;

export type LocalAuthProfile = {
  ok: boolean;
  user_id: string;
  tenant_id: string;
  roles: string[];
  permissions: string[];
  is_admin: boolean;
  provider_billing_mode?: "default" | "internal" | "self_hosted";
  effective_provider_billing_mode?: "internal" | "self_hosted";
};

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

function emitAuthBootstrapChange(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(AUTH_BOOTSTRAP_EVENT));
}

function setAuthBootstrapStatus(status: AuthBootstrapStatus): void {
  if (authBootstrapStatus === status) return;
  authBootstrapStatus = status;
  emitAuthBootstrapChange();
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

function subscribeAuthBootstrap(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onBootstrapChange = () => listener();
  window.addEventListener(AUTH_BOOTSTRAP_EVENT, onBootstrapChange);
  return () => {
    window.removeEventListener(AUTH_BOOTSTRAP_EVENT, onBootstrapChange);
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
  setAuthBootstrapStatus(token && token.trim() ? "ready" : "ready");
  emitAuthChange();
}

export function useAccessToken(): string {
  return useSyncExternalStore(subscribeAuth, readAccessToken, () => "");
}

export function useAuthBootstrapStatus(): AuthBootstrapStatus {
  return useSyncExternalStore(subscribeAuthBootstrap, () => authBootstrapStatus, () => "ready");
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

export async function ensureAuthReady(): Promise<void> {
  const token = readAccessToken().trim();
  if (!token) {
    authBootstrapPromise = null;
    setAuthBootstrapStatus("ready");
    return;
  }
  if (authBootstrapStatus === "ready") return;
  if (authBootstrapPromise) {
    await authBootstrapPromise;
    return;
  }
  setAuthBootstrapStatus("checking");
  authBootstrapPromise = (async () => {
    try {
      const verified = await verifyLocalAuthSession(token);
      if (!verified && readAccessToken().trim() === token) {
        setAccessToken(null);
      }
    } finally {
      authBootstrapPromise = null;
      setAuthBootstrapStatus("ready");
    }
  })();
  await authBootstrapPromise;
}

export async function fetchLocalAuthProfile(token: string): Promise<LocalAuthProfile | null> {
  const normalized = token.trim();
  if (!normalized) return null;
  try {
    const response = await fetch(`${API_BASE}/auth/me`, {
      headers: {
        Authorization: `Bearer ${normalized}`,
      },
    });
    if (!response.ok) return null;
    return (await response.json()) as LocalAuthProfile;
  } catch {
    return null;
  }
}
