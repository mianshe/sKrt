import type { NativeStorageBridge } from "./clientPersistence";
import type { LocalGuestDoc } from "./localGuestDocuments";
import type { LocalUserBackupRecord } from "./localUserBackup";

type SerializedGuestDoc = {
  id: string;
  filename: string;
  size: number;
  createdAt: string;
  blobBase64: string;
  blobType: string;
};

type SerializedUserBackup = {
  id: string;
  taskId: number;
  filename: string;
  createdAt: string;
  originalBlobBase64: string;
  originalBlobType: string;
  processJson: string;
};

type CapacitorFilesystemLike = {
  mkdir: (options: Record<string, unknown>) => Promise<unknown>;
  readFile: (options: Record<string, unknown>) => Promise<{ data?: string }>;
  writeFile: (options: Record<string, unknown>) => Promise<unknown>;
};

type TauriInvokeLike = (command: string, args?: Record<string, unknown>) => Promise<unknown>;

const STORAGE_ROOT = "skrt-native-storage";
const GUEST_DOCS_FILE = "guest-documents.json";
const USER_BACKUPS_FILE = "user-backups.json";
const CAPACITOR_DIRECTORY_DATA = "DATA";

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("blob_to_base64_failed"));
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      const commaIndex = result.indexOf(",");
      resolve(commaIndex >= 0 ? result.slice(commaIndex + 1) : result);
    };
    reader.readAsDataURL(blob);
  });
}

function base64ToBlob(base64: string, type: string): Blob {
  const binary = atob(base64 || "");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Blob([bytes], { type: type || "application/octet-stream" });
}

async function serializeGuestDocs(entries: LocalGuestDoc[]): Promise<SerializedGuestDoc[]> {
  return Promise.all(
    entries.map(async (entry) => ({
      id: entry.id,
      filename: entry.filename,
      size: entry.size,
      createdAt: entry.createdAt,
      blobBase64: await blobToBase64(entry.blob),
      blobType: entry.blob.type || "application/octet-stream",
    }))
  );
}

async function serializeUserBackups(entries: LocalUserBackupRecord[]): Promise<SerializedUserBackup[]> {
  return Promise.all(
    entries.map(async (entry) => ({
      id: entry.id,
      taskId: entry.taskId,
      filename: entry.filename,
      createdAt: entry.createdAt,
      originalBlobBase64: await blobToBase64(entry.originalBlob),
      originalBlobType: entry.originalBlob.type || "application/octet-stream",
      processJson: entry.processJson,
    }))
  );
}

function deserializeGuestDocs(entries: SerializedGuestDoc[]): LocalGuestDoc[] {
  return entries.map((entry) => ({
    id: entry.id,
    filename: entry.filename,
    size: entry.size,
    createdAt: entry.createdAt,
    blob: base64ToBlob(entry.blobBase64, entry.blobType),
  }));
}

function deserializeUserBackups(entries: SerializedUserBackup[]): LocalUserBackupRecord[] {
  return entries.map((entry) => ({
    id: entry.id,
    taskId: entry.taskId,
    filename: entry.filename,
    createdAt: entry.createdAt,
    originalBlob: base64ToBlob(entry.originalBlobBase64, entry.originalBlobType),
    processJson: entry.processJson,
  }));
}

function parseJsonArray<T>(raw: string | null | undefined): T[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function getTauriInvoke(): TauriInvokeLike | null {
  if (typeof window === "undefined") return null;
  const tauriWindow = window as Window & {
    __TAURI__?: {
      core?: {
        invoke?: TauriInvokeLike;
      };
    };
    __TAURI_INTERNALS__?: {
      invoke?: TauriInvokeLike;
    };
  };
  if (typeof tauriWindow.__TAURI__?.core?.invoke === "function") return tauriWindow.__TAURI__.core.invoke;
  if (typeof tauriWindow.__TAURI_INTERNALS__?.invoke === "function") return tauriWindow.__TAURI_INTERNALS__.invoke;
  return null;
}

function getCapacitorFilesystem(): CapacitorFilesystemLike | null {
  if (typeof window === "undefined") return null;
  const capacitorWindow = window as Window & {
    Capacitor?: {
      Plugins?: {
        Filesystem?: CapacitorFilesystemLike;
      };
    };
  };
  return capacitorWindow.Capacitor?.Plugins?.Filesystem ?? null;
}

function createTauriBridge(): NativeStorageBridge {
  const readText = async (filename: string): Promise<string> => {
    const invoke = getTauriInvoke();
    if (!invoke) return "[]";
    const result = await invoke("native_storage_read_text", { filename });
    return typeof result === "string" ? result : "[]";
  };

  const writeText = async (filename: string, content: string): Promise<void> => {
    const invoke = getTauriInvoke();
    if (!invoke) throw new Error("tauri_invoke_unavailable");
    await invoke("native_storage_write_text", { filename, content });
  };

  const listGuestDocuments = async (): Promise<LocalGuestDoc[]> => {
    const raw = await readText(GUEST_DOCS_FILE);
    return deserializeGuestDocs(parseJsonArray<SerializedGuestDoc>(raw));
  };

  const saveGuestDocuments = async (entries: LocalGuestDoc[]): Promise<void> => {
    const payload = await serializeGuestDocs(entries);
    await writeText(GUEST_DOCS_FILE, JSON.stringify(payload));
  };

  const listUserBackups = async (): Promise<LocalUserBackupRecord[]> => {
    const raw = await readText(USER_BACKUPS_FILE);
    return deserializeUserBackups(parseJsonArray<SerializedUserBackup>(raw));
  };

  const saveUserBackups = async (entries: LocalUserBackupRecord[]): Promise<void> => {
    const payload = await serializeUserBackups(entries);
    await writeText(USER_BACKUPS_FILE, JSON.stringify(payload));
  };

  return {
    runtime: "desktop-tauri",
    storageTarget: "device-native",
    listGuestDocuments,
    putGuestDocument: async (entry) => {
      const current = await listGuestDocuments();
      const next = current.filter((item) => item.id !== entry.id);
      next.push(entry);
      await saveGuestDocuments(next);
    },
    deleteGuestDocument: async (id) => {
      const current = await listGuestDocuments();
      await saveGuestDocuments(current.filter((item) => item.id !== id));
    },
    listUserBackups,
    putUserBackup: async (entry) => {
      const current = await listUserBackups();
      const next = current.filter((item) => item.id !== entry.id);
      next.push(entry);
      await saveUserBackups(next);
    },
    deleteUserBackup: async (id) => {
      const current = await listUserBackups();
      await saveUserBackups(current.filter((item) => item.id !== id));
    },
  };
}

function createCapacitorBridge(): NativeStorageBridge {
  const ensureRootDir = async (): Promise<void> => {
    const filesystem = getCapacitorFilesystem();
    if (!filesystem) throw new Error("capacitor_filesystem_unavailable");
    try {
      await filesystem.mkdir({
        path: STORAGE_ROOT,
        directory: CAPACITOR_DIRECTORY_DATA,
        recursive: true,
      });
    } catch {
      // ignore already-exists errors
    }
  };

  const readText = async (filename: string): Promise<string> => {
    const filesystem = getCapacitorFilesystem();
    if (!filesystem) return "[]";
    await ensureRootDir();
    try {
      const result = await filesystem.readFile({
        path: `${STORAGE_ROOT}/${filename}`,
        directory: CAPACITOR_DIRECTORY_DATA,
      });
      return typeof result?.data === "string" ? result.data : "[]";
    } catch {
      return "[]";
    }
  };

  const writeText = async (filename: string, content: string): Promise<void> => {
    const filesystem = getCapacitorFilesystem();
    if (!filesystem) throw new Error("capacitor_filesystem_unavailable");
    await ensureRootDir();
    await filesystem.writeFile({
      path: `${STORAGE_ROOT}/${filename}`,
      directory: CAPACITOR_DIRECTORY_DATA,
      data: content,
    });
  };

  const listGuestDocuments = async (): Promise<LocalGuestDoc[]> => {
    const raw = await readText(GUEST_DOCS_FILE);
    return deserializeGuestDocs(parseJsonArray<SerializedGuestDoc>(raw));
  };

  const saveGuestDocuments = async (entries: LocalGuestDoc[]): Promise<void> => {
    const payload = await serializeGuestDocs(entries);
    await writeText(GUEST_DOCS_FILE, JSON.stringify(payload));
  };

  const listUserBackups = async (): Promise<LocalUserBackupRecord[]> => {
    const raw = await readText(USER_BACKUPS_FILE);
    return deserializeUserBackups(parseJsonArray<SerializedUserBackup>(raw));
  };

  const saveUserBackups = async (entries: LocalUserBackupRecord[]): Promise<void> => {
    const payload = await serializeUserBackups(entries);
    await writeText(USER_BACKUPS_FILE, JSON.stringify(payload));
  };

  return {
    runtime: "android-capacitor",
    storageTarget: "device-native",
    listGuestDocuments,
    putGuestDocument: async (entry) => {
      const current = await listGuestDocuments();
      const next = current.filter((item) => item.id !== entry.id);
      next.push(entry);
      await saveGuestDocuments(next);
    },
    deleteGuestDocument: async (id) => {
      const current = await listGuestDocuments();
      await saveGuestDocuments(current.filter((item) => item.id !== id));
    },
    listUserBackups,
    putUserBackup: async (entry) => {
      const current = await listUserBackups();
      const next = current.filter((item) => item.id !== entry.id);
      next.push(entry);
      await saveUserBackups(next);
    },
    deleteUserBackup: async (id) => {
      const current = await listUserBackups();
      await saveUserBackups(current.filter((item) => item.id !== id));
    },
  };
}

export function installNativeStorageBridge(): void {
  if (typeof window === "undefined" || window.__SKRT_NATIVE_STORAGE__) return;
  if (getTauriInvoke()) {
    window.__SKRT_NATIVE_STORAGE__ = createTauriBridge();
    return;
  }
  if (getCapacitorFilesystem()) {
    window.__SKRT_NATIVE_STORAGE__ = createCapacitorBridge();
  }
}
