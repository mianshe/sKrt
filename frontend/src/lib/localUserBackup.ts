/**
 * 已登录用户：在「云端解析 + 本机备份」模式下，将原件副本与解析过程摘要存入 IndexedDB（与游客 localGuestDocuments 分库）。
 */
const DB_NAME = "xm1_user_backups";
const STORE = "backups";
const DB_VER = 1;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VER);
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve(req.result);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "id" });
      }
    };
  });
}

export type LocalUserBackupRecord = {
  id: string;
  taskId: number;
  filename: string;
  createdAt: string;
  /** 上传原件副本 */
  originalBlob: Blob;
  /** JSON.stringify(LocalProcessSnapshot) */
  processJson: string;
};

export function localDraftProcessJson(): string {
  return JSON.stringify({ version: 1, kind: "local_draft", savedAt: new Date().toISOString() });
}

export type LocalProcessSnapshot = {
  version: 1;
  savedAt: string;
  task: Record<string, unknown>;
  document: Record<string, unknown> | null;
};

export async function listLocalUserBackups(): Promise<LocalUserBackupRecord[]> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const st = tx.objectStore(STORE);
    const req = st.getAll();
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve((req.result as LocalUserBackupRecord[]) || []);
  });
}

export async function putLocalUserBackup(entry: LocalUserBackupRecord): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.objectStore(STORE).put(entry);
  });
}

export async function deleteLocalUserBackup(id: string): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.objectStore(STORE).delete(id);
  });
}

export function downloadProcessSnapshotJson(filename: string, snapshot: LocalProcessSnapshot): void {
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${filename.replace(/[\\/]/g, "_")}_process.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function downloadOriginalBlob(filename: string, blob: Blob): void {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename.replace(/[\\/]/g, "_");
  a.click();
  URL.revokeObjectURL(a.href);
}
