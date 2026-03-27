import { useCallback, useEffect, useState } from "react";

const API_BASE = (globalThis as any).__API_BASE__ || "http://localhost:8000";
const TENANT_KEY = "xm_tenant_id";
const CLIENT_KEY = "xm_client_id";

function tenantId(): string {
  try {
    return localStorage.getItem(TENANT_KEY)?.trim() || "public";
  } catch {
    return "public";
  }
}

function withTenantHeaders(base?: Record<string, string>): Record<string, string> {
  let clientId = "";
  try {
    clientId = localStorage.getItem(CLIENT_KEY) || "";
    if (!clientId) {
      clientId = crypto.randomUUID();
      localStorage.setItem(CLIENT_KEY, clientId);
    }
  } catch {
    clientId = "";
  }
  return { ...(base || {}), "X-Tenant-Id": tenantId(), ...(clientId ? { "X-Client-Id": clientId } : {}) };
}

/** 单块大小（与后端流式读缓冲匹配，不宜过大） */
const CHUNK_BYTES = 4 * 1024 * 1024;
/** 大于等于此字节数走分片上传，避免单次 POST 过大导致超时或后端崩溃 */
const CHUNK_UPLOAD_THRESHOLD = 1024 * 1024;

const POLL_MS = 2000;
const POLL_MAX_MS = 45 * 60 * 1000;

export type DocumentItem = {
  id: number;
  filename: string;
  title: string;
  discipline: string;
  document_type: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type ExamChunkUploadResult = {
  filename?: string;
  discipline?: string;
  document_type?: string;
  analysis?: unknown;
  tasks?: unknown[];
};

export type IngestionTaskPoll = {
  extract: number;
  index: number;
  overall: number;
  phase?: string;
  sec_per_mb_extract?: number | null;
  sec_per_page_extract?: number | null;
  extract_duration_sec?: number | null;
  index_duration_sec?: number | null;
  rollup_avg_sec_per_mb_extract?: number | null;
  rollup_avg_sec_per_page_extract?: number | null;
  rollup_task_count?: number;
};

type UploadTaskPayload = {
  /** 原始行数据用 id；`_normalize_task` 返回用 task_id */
  id?: number;
  task_id?: number;
  filename?: string;
  discipline?: string;
  document_type?: string;
  page_count?: number;
  status?: string;
  phase?: string;
  progress_percent?: number;
  extract_progress_percent?: number;
  index_progress_percent?: number;
  error_message?: string;
  retries?: number;
  sec_per_mb_extract?: number | null;
  sec_per_page_extract?: number | null;
  extract_duration_sec?: number | null;
  index_duration_sec?: number | null;
  rollup_avg_sec_per_mb_extract?: number | null;
  rollup_avg_sec_per_page_extract?: number | null;
  rollup_task_count?: number;
};

function pickTaskId(row: { id?: number; task_id?: number } | undefined): number | undefined {
  if (!row) return undefined;
  const v = row.task_id ?? row.id;
  if (typeof v !== "number" || Number.isNaN(v)) return undefined;
  return v;
}

export type UploadTaskItem = {
  task_id: number;
  filename: string;
  discipline: string;
  document_type: string;
  page_count: number;
  status: string;
  phase: string;
  progress_percent: number;
  error_message: string;
  retries: number;
};

function normalizeTask(task: UploadTaskPayload): UploadTaskItem {
  return {
    task_id: Number(pickTaskId(task) || 0),
    filename: String(task.filename || ""),
    discipline: String(task.discipline || "all"),
    document_type: String(task.document_type || "academic"),
    page_count: Number(task.page_count || 0),
    status: String(task.status || "queued"),
    phase: String(task.phase || task.status || "queued"),
    progress_percent: Number(task.progress_percent || 0),
    error_message: String(task.error_message || ""),
    retries: Number(task.retries || 0),
  };
}

function backendHint(): string {
  return `请确认后端已启动并可打开 ${API_BASE}/docs；从手机/其他电脑访问时请设置环境变量 VITE_API_BASE 为可访问的后端地址。`;
}

function payloadToIngestionPoll(t: UploadTaskPayload): IngestionTaskPoll {
  const ex = typeof t.extract_progress_percent === "number" ? t.extract_progress_percent : 0;
  const ix = typeof t.index_progress_percent === "number" ? t.index_progress_percent : 0;
  const ov = typeof t.progress_percent === "number" ? t.progress_percent : 0;
  return {
    extract: ex,
    index: ix,
    overall: ov,
    phase: t.phase,
    sec_per_mb_extract: t.sec_per_mb_extract ?? null,
    sec_per_page_extract: t.sec_per_page_extract ?? null,
    extract_duration_sec: t.extract_duration_sec ?? null,
    index_duration_sec: t.index_duration_sec ?? null,
    rollup_avg_sec_per_mb_extract: t.rollup_avg_sec_per_mb_extract ?? null,
    rollup_avg_sec_per_page_extract: t.rollup_avg_sec_per_page_extract ?? null,
    rollup_task_count: t.rollup_task_count,
  };
}

async function pollUploadTask(
  taskId: number,
  onPercent?: (n: number) => void,
  onIngestion?: (p: IngestionTaskPoll) => void
): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < POLL_MAX_MS) {
    let resp: Response;
    try {
      resp = await fetch(`${API_BASE}/upload/tasks/${taskId}`, { headers: withTenantHeaders() });
    } catch {
      throw new Error(`无法连接后端（${API_BASE}/upload/tasks/${taskId}）。${backendHint()}`);
    }
    if (!resp.ok) {
      throw new Error(`无法连接后端（${API_BASE}/upload/tasks/${taskId}，HTTP ${resp.status}）。${backendHint()}`);
    }
    const t = (await resp.json()) as UploadTaskPayload;
    const poll = payloadToIngestionPoll(t);
    onPercent?.(poll.overall);
    onIngestion?.(poll);
    if (t.status === "completed") return;
    if (t.status === "failed") {
      throw new Error(t.error_message || "文档入库失败");
    }
    await new Promise((r) => setTimeout(r, POLL_MS));
  }
  throw new Error("入库等待超时，请稍后刷新文档列表查看是否已处理完成。");
}

async function getTaskById(taskId: number): Promise<UploadTaskItem> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}/upload/tasks/${taskId}`, { headers: withTenantHeaders() });
  } catch {
    throw new Error(`无法连接后端（${API_BASE}/upload/tasks/${taskId}）。${backendHint()}`);
  }
  if (!resp.ok) {
    throw new Error(`无法连接后端（${API_BASE}/upload/tasks/${taskId}，HTTP ${resp.status}）。${backendHint()}`);
  }
  const data = (await resp.json()) as UploadTaskPayload;
  return normalizeTask(data);
}

async function createSingleFileTask(
  file: File,
  discipline: string,
  documentType: string,
  useGpuOcr: boolean
): Promise<UploadTaskItem> {
  const form = new FormData();
  form.append("files", file);
  const url = new URL(`${API_BASE}/upload/tasks`);
  url.searchParams.set("discipline", discipline);
  url.searchParams.set("document_type", documentType);
  url.searchParams.set("use_gpu_ocr", useGpuOcr ? "1" : "0");
  let resp: Response;
  try {
    resp = await fetch(url, { method: "POST", body: form, headers: withTenantHeaders() });
  } catch {
    throw new Error(`无法连接后端（${API_BASE}/upload/tasks）。${backendHint()}`);
  }
  if (!resp.ok) {
    const details = await resp.text();
    throw new Error(details || "上传失败");
  }
  const data = (await resp.json()) as { tasks?: Array<{ id?: number; task_id?: number }> };
  const id = pickTaskId(data.tasks?.[0]);
  if (id == null) {
    throw new Error("未返回有效任务ID");
  }
  return await getTaskById(Number(id));
}

async function createSingleFileChunkTask(
  file: File,
  discipline: string,
  documentType: string,
  useGpuOcr: boolean,
  onUploadPercent?: (n: number) => void
): Promise<UploadTaskItem> {
  const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_BYTES));
  const initBody = {
    filename: file.name,
    total_size: file.size,
    total_chunks: totalChunks,
    discipline,
    document_type: documentType,
    purpose: "docs" as const,
    use_gpu_ocr: useGpuOcr,
  };
  let initResp: Response;
  try {
    initResp = await fetch(`${API_BASE}/upload/chunks/init`, {
      method: "POST",
      headers: withTenantHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(initBody),
    });
  } catch {
    throw new Error(`无法连接后端（${API_BASE}/upload/chunks/init）。${backendHint()}`);
  }
  if (!initResp.ok) {
    const t = await initResp.text();
    throw new Error(t || "分片初始化失败");
  }
  const initJson = (await initResp.json()) as { upload_id: string };
  const { upload_id } = initJson;

  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_BYTES;
    const end = Math.min(file.size, start + CHUNK_BYTES);
    const blob = file.slice(start, end);
    const form = new FormData();
    form.append("chunk", blob, file.name);
    let putResp: Response;
    try {
      putResp = await fetch(`${API_BASE}/upload/chunks/${upload_id}?chunk_index=${i}`, {
        method: "PUT",
        headers: withTenantHeaders(),
        body: form,
      });
    } catch {
      throw new Error(`无法连接后端（分片 ${i + 1}/${totalChunks}）。${backendHint()}`);
    }
    if (!putResp.ok) {
      const t = await putResp.text();
      throw new Error(t || `分片 ${i} 上传失败`);
    }
    const pct = Math.round(((i + 1) / totalChunks) * 85);
    onUploadPercent?.(pct);
  }

  let completeResp: Response;
  try {
    completeResp = await fetch(`${API_BASE}/upload/chunks/${upload_id}/complete`, {
      method: "POST",
      headers: withTenantHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        discipline,
        document_type: documentType,
        purpose: "docs",
        use_gpu_ocr: useGpuOcr,
      }),
    });
  } catch {
    throw new Error(`无法连接后端（${API_BASE}/upload/chunks/.../complete）。${backendHint()}`);
  }
  if (!completeResp.ok) {
    const t = await completeResp.text();
    throw new Error(t || "分片合并失败");
  }
  const done = (await completeResp.json()) as { tasks?: Array<{ id?: number; task_id?: number }> };
  const taskId = pickTaskId(done.tasks?.[0]);
  onUploadPercent?.(90);
  if (taskId == null) {
    throw new Error("分片完成后未返回任务ID");
  }
  const task = await getTaskById(Number(taskId));
  onUploadPercent?.(100);
  return task;
}

/**
 * 试卷/问答区：大文件走分片 + complete(exam)，小文件走 /exam/upload
 */
export async function uploadExamByChunks(
  file: File,
  discipline: string,
  onProgress?: (percent: number) => void
): Promise<ExamChunkUploadResult> {
  const disc = discipline.trim() || "all";
  if (file.size < CHUNK_UPLOAD_THRESHOLD) {
    onProgress?.(10);
    const form = new FormData();
    form.append("file", file);
    const url = new URL(`${API_BASE}/exam/upload`);
    url.searchParams.set("discipline", disc);
    let resp: Response;
    try {
      resp = await fetch(url, { method: "POST", body: form, headers: withTenantHeaders() });
    } catch {
      throw new Error(`无法连接后端（${API_BASE}/exam/upload）。${backendHint()}`);
    }
    if (!resp.ok) {
      const t = await resp.text();
      throw new Error(t || "上传失败");
    }
    onProgress?.(100);
    return (await resp.json()) as ExamChunkUploadResult;
  }

  const totalChunks = Math.max(1, Math.ceil(file.size / CHUNK_BYTES));
  const initResp = await fetch(`${API_BASE}/upload/chunks/init`, {
    method: "POST",
    headers: withTenantHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      filename: file.name,
      total_size: file.size,
      total_chunks: totalChunks,
      discipline: disc,
      document_type: "exam",
      purpose: "exam",
    }),
  });
  if (!initResp.ok) {
    const t = await initResp.text();
    throw new Error(t || "分片初始化失败");
  }
  const { upload_id } = (await initResp.json()) as { upload_id: string };

  for (let i = 0; i < totalChunks; i++) {
    const start = i * CHUNK_BYTES;
    const end = Math.min(file.size, start + CHUNK_BYTES);
    const blob = file.slice(start, end);
    const form = new FormData();
    form.append("chunk", blob, file.name);
    const putResp = await fetch(`${API_BASE}/upload/chunks/${upload_id}?chunk_index=${i}`, {
      method: "PUT",
      headers: withTenantHeaders(),
      body: form,
    });
    if (!putResp.ok) {
      const t = await putResp.text();
      throw new Error(t || `分片 ${i} 上传失败`);
    }
    onProgress?.(Math.round(((i + 1) / totalChunks) * 90));
  }

  const completeResp = await fetch(`${API_BASE}/upload/chunks/${upload_id}/complete`, {
    method: "POST",
    headers: withTenantHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      discipline: disc,
      document_type: "exam",
      purpose: "exam",
    }),
  });
  if (!completeResp.ok) {
    const t = await completeResp.text();
    throw new Error(t || "合并失败");
  }
  onProgress?.(100);
  return (await completeResp.json()) as ExamChunkUploadResult;
}

export function useDocuments() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>("");

  const refreshDocuments = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const resp = await fetch(`${API_BASE}/documents`, { headers: withTenantHeaders() });
      if (!resp.ok) throw new Error("文档列表加载失败");
      const data = await resp.json();
      setDocuments(data.documents || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "未知错误");
    } finally {
      setLoading(false);
    }
  }, []);

  const createUploadTasks = useCallback(
    async (
      files: File[],
      discipline: string,
      documentType: string,
      onUploadProgress?: (percent: number) => void,
      options?: { use_gpu_ocr?: boolean }
    ): Promise<UploadTaskItem[]> => {
      if (!files.length) return [];
      const normalizedDiscipline = (discipline.trim().toLowerCase() || "all");
      const useGpuOcr = Boolean(options?.use_gpu_ocr);
      const all: UploadTaskItem[] = [];
      let completed = 0;
      for (const file of files) {
        const task =
          file.size >= CHUNK_UPLOAD_THRESHOLD
            ? await createSingleFileChunkTask(file, normalizedDiscipline, documentType, useGpuOcr, (p) => {
                const base = Math.round((completed / files.length) * 100);
                const local = Math.round(p / files.length);
                onUploadProgress?.(Math.min(99, base + local));
              })
            : await createSingleFileTask(file, normalizedDiscipline, documentType, useGpuOcr);
        completed += 1;
        onUploadProgress?.(Math.round((completed / files.length) * 100));
        all.push(task);
      }
      return all;
    },
    []
  );

  const getUploadTask = useCallback(async (taskId: number): Promise<UploadTaskItem> => {
    return await getTaskById(taskId);
  }, []);

  const uploadFiles = useCallback(
    async (
      files: File[],
      discipline: string,
      documentType: string,
      onIngestionProgress?: (p: IngestionTaskPoll) => void
    ) => {
      if (!files.length) return;
      setLoading(true);
      setError("");
      try {
        const tasks = await createUploadTasks(files, discipline, documentType);
        for (const task of tasks) {
          await pollUploadTask(task.task_id, undefined, onIngestionProgress);
        }
        await refreshDocuments();
      } catch (e) {
        setError(e instanceof Error ? e.message : "上传失败");
      } finally {
        setLoading(false);
      }
    },
    [createUploadTasks, refreshDocuments]
  );

  const deleteDocument = useCallback(
    async (id: number) => {
      setError("");
      const resp = await fetch(`${API_BASE}/documents/${id}`, { method: "DELETE", headers: withTenantHeaders() });
      if (!resp.ok) {
        const details = await resp.text();
        throw new Error(details || "删除失败");
      }
      await refreshDocuments();
    },
    [refreshDocuments]
  );

  useEffect(() => {
    refreshDocuments();
  }, [refreshDocuments]);

  return {
    documents,
    loading,
    error,
    refreshDocuments,
    createUploadTasks,
    getUploadTask,
    uploadFiles,
    /** 与 createUploadTasks 相同，便于组件使用 onCreateUploadTasks 命名 */
    onCreateUploadTasks: createUploadTasks,
    deleteDocument,
    uploadExamByChunks,
  };
}
