import {
  deleteLocalGuestDocument,
  listLocalGuestDocuments,
  putLocalGuestDocument,
  type LocalGuestDoc,
} from "./localGuestDocuments";
import {
  deleteLocalUserBackup,
  listLocalUserBackups,
  putLocalUserBackup,
  type LocalUserBackupRecord,
} from "./localUserBackup";

export type ClientRuntimeKind = "web-browser" | "desktop-tauri" | "android-capacitor";
export type ClientStorageTarget = "browser-indexeddb" | "device-native";

export type NativeStorageBridge = {
  runtime: Exclude<ClientRuntimeKind, "web-browser">;
  storageTarget?: ClientStorageTarget;
  listGuestDocuments: () => Promise<LocalGuestDoc[]>;
  putGuestDocument: (entry: LocalGuestDoc) => Promise<void>;
  deleteGuestDocument: (id: string) => Promise<void>;
  listUserBackups: () => Promise<LocalUserBackupRecord[]>;
  putUserBackup: (entry: LocalUserBackupRecord) => Promise<void>;
  deleteUserBackup: (id: string) => Promise<void>;
};

export type ClientPersistenceInfo = {
  runtime: ClientRuntimeKind;
  label: string;
  storageTarget: ClientStorageTarget;
  storageLabel: string;
  nativeBridgeReady: boolean;
};

declare global {
  interface Window {
    __SKRT_NATIVE_STORAGE__?: NativeStorageBridge;
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
    Capacitor?: unknown;
  }
}

function hasNativeBridge(bridge: NativeStorageBridge | undefined): bridge is NativeStorageBridge {
  if (!bridge) return false;
  return (
    typeof bridge.listGuestDocuments === "function" &&
    typeof bridge.putGuestDocument === "function" &&
    typeof bridge.deleteGuestDocument === "function" &&
    typeof bridge.listUserBackups === "function" &&
    typeof bridge.putUserBackup === "function" &&
    typeof bridge.deleteUserBackup === "function"
  );
}

function getNativeBridge(): NativeStorageBridge | null {
  if (typeof window === "undefined") return null;
  return hasNativeBridge(window.__SKRT_NATIVE_STORAGE__) ? window.__SKRT_NATIVE_STORAGE__ : null;
}

function detectRuntimeKind(): ClientRuntimeKind {
  const bridge = getNativeBridge();
  if (bridge) return bridge.runtime;
  if (typeof window === "undefined") return "web-browser";
  if (window.Capacitor) return "android-capacitor";
  if (window.__TAURI__ || window.__TAURI_INTERNALS__) return "desktop-tauri";
  return "web-browser";
}

export function getClientPersistenceInfo(): ClientPersistenceInfo {
  const bridge = getNativeBridge();
  const runtime = detectRuntimeKind();
  const storageTarget: ClientStorageTarget = bridge?.storageTarget || (bridge ? "device-native" : "browser-indexeddb");

  if (runtime === "desktop-tauri") {
    return {
      runtime,
      label: "桌面端 exe",
      storageTarget,
      storageLabel: storageTarget === "device-native" ? "当前电脑设备目录" : "当前应用内浏览器数据",
      nativeBridgeReady: Boolean(bridge),
    };
  }

  if (runtime === "android-capacitor") {
    return {
      runtime,
      label: "Android app",
      storageTarget,
      storageLabel: storageTarget === "device-native" ? "当前手机设备目录" : "当前应用内浏览器数据",
      nativeBridgeReady: Boolean(bridge),
    };
  }

  return {
    runtime,
    label: "网页浏览器",
    storageTarget,
    storageLabel: "当前浏览器数据",
    nativeBridgeReady: false,
  };
}

export function isNativeDeviceStorageRuntime(): boolean {
  const info = getClientPersistenceInfo();
  return info.runtime !== "web-browser" && info.storageTarget === "device-native" && info.nativeBridgeReady;
}

export async function listClientGuestDocuments(): Promise<LocalGuestDoc[]> {
  const bridge = getNativeBridge();
  return bridge ? bridge.listGuestDocuments() : listLocalGuestDocuments();
}

export async function putClientGuestDocument(entry: LocalGuestDoc): Promise<void> {
  const bridge = getNativeBridge();
  if (bridge) {
    await bridge.putGuestDocument(entry);
    return;
  }
  await putLocalGuestDocument(entry);
}

export async function deleteClientGuestDocument(id: string): Promise<void> {
  const bridge = getNativeBridge();
  if (bridge) {
    await bridge.deleteGuestDocument(id);
    return;
  }
  await deleteLocalGuestDocument(id);
}

export async function listClientUserBackups(): Promise<LocalUserBackupRecord[]> {
  const bridge = getNativeBridge();
  return bridge ? bridge.listUserBackups() : listLocalUserBackups();
}

export async function putClientUserBackup(entry: LocalUserBackupRecord): Promise<void> {
  const bridge = getNativeBridge();
  if (bridge) {
    await bridge.putUserBackup(entry);
    return;
  }
  await putLocalUserBackup(entry);
}

export async function deleteClientUserBackup(id: string): Promise<void> {
  const bridge = getNativeBridge();
  if (bridge) {
    await bridge.deleteUserBackup(id);
    return;
  }
  await deleteLocalUserBackup(id);
}
