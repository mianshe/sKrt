/**
 * 未登录时仅在浏览器内保存资料元数据（IndexedDB），不上传服务器。
 * 与登录后服务端知识库并行存在；匿名页可展示本地列表作提示。
 */
const DB_NAME = "xm1_guest_docs";
const STORE = "files";
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

export type LocalGuestDoc = {
  id: string;
  filename: string;
  size: number;
  createdAt: string;
  blob: Blob;
};

export async function listLocalGuestDocuments(): Promise<LocalGuestDoc[]> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const st = tx.objectStore(STORE);
    const req = st.getAll();
    req.onerror = () => reject(req.error);
    req.onsuccess = () => resolve((req.result as LocalGuestDoc[]) || []);
  });
}

export async function putLocalGuestDocument(entry: LocalGuestDoc): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.objectStore(STORE).put(entry);
  });
}

export async function deleteLocalGuestDocument(id: string): Promise<void> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.objectStore(STORE).delete(id);
  });
}
