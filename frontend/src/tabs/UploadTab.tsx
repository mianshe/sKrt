import { DragEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  DocumentItem,
  UploadTaskItem,
  fetchTenantQuotaStatus,
  buildLocalProcessSnapshot,
  CHUNK_UPLOAD_THRESHOLD,
  downloadCloudDocumentOriginal,
  fileKeyForUpload,
  type OcrMode,
  type TenantQuotaStatus,
} from "../hooks/useDocuments";
import { getAccessToken, useAccessToken } from "../lib/auth";
import {
  listLocalGuestDocuments,
  putLocalGuestDocument,
  deleteLocalGuestDocument,
  type LocalGuestDoc,
} from "../lib/localGuestDocuments";
import {
  deleteLocalUserBackup,
  listLocalUserBackups,
  putLocalUserBackup,
  localDraftProcessJson,
  type LocalUserBackupRecord,
} from "../lib/localUserBackup";

type Props = {
  documents: DocumentItem[];
  loading: boolean;
  error: string;
  onCreateUploadTasks: (
    files: File[],
    discipline: string,
    documentType: string,
    onUploadProgress?: (percent: number) => void,
    options?: { ocr_mode?: OcrMode; external_ocr_confirmed?: boolean }
  ) => Promise<UploadTaskItem[]>;
  onGetTask: (taskId: number) => Promise<UploadTaskItem>;
  onDelete: (id: number) => Promise<void>;
  onRefresh: () => Promise<void>;
  authLocalEnabled?: boolean;
  authSession?: number;
};

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function splitIndexStage(indexPercent: number, phase: string, status: string): { vector: number; indexBuild: number } {
  if (status === "completed" || phase === "completed") {
    return { vector: 100, indexBuild: 100 };
  }
  if (phase !== "indexing") {
    return { vector: 0, indexBuild: 0 };
  }
  const normalized = clampPercent(indexPercent);
  if (normalized <= 80) {
    return {
      vector: clampPercent((normalized / 80) * 100),
      indexBuild: 0,
    };
  }
  return {
    vector: 100,
    indexBuild: clampPercent(((normalized - 80) / 20) * 100),
  };
}

async function mergeCloudIntoLocalBackups(
  tasks: UploadTaskItem[],
  files: File[],
  localIdByKey: Map<string, string>
): Promise<void> {
  const token = getAccessToken();
  if (!token) return;
  for (let i = 0; i < tasks.length; i++) {
    const task = tasks[i];
    const file = files[i];
    if (!file) continue;
    const k = fileKeyForUpload(file);
    const localId = localIdByKey.get(k);
    if (!localId) continue;
    const snapshot = await buildLocalProcessSnapshot(task.task_id);
    await putLocalUserBackup({
      id: localId,
      taskId: task.task_id,
      filename: file.name,
      createdAt: new Date().toISOString(),
      originalBlob: file,
      processJson: JSON.stringify(snapshot),
    });
  }
}

function UploadTab({
  documents,
  loading,
  error,
  onCreateUploadTasks,
  onGetTask,
  onDelete,
  onRefresh,
  authLocalEnabled = false,
  authSession = 0,
}: Props) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [parseProgress, setParseProgress] = useState(0);
  const [extractProgress, setExtractProgress] = useState(0);
  const [vectorProgress, setVectorProgress] = useState(0);
  const [indexBuildProgress, setIndexBuildProgress] = useState(0);
  const [phaseText, setPhaseText] = useState("");
  const [slowHint, setSlowHint] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [localError, setLocalError] = useState("");
  const [localInfo, setLocalInfo] = useState("");
  const [dragging, setDragging] = useState(false);
  const [ocrMode, setOcrMode] = useState<OcrMode>("standard");
  const [uploadToCloud, setUploadToCloud] = useState(false);
  const [guestLocalDocs, setGuestLocalDocs] = useState<LocalGuestDoc[]>([]);
  const [userBackups, setUserBackups] = useState<LocalUserBackupRecord[]>([]);
  const [quota, setQuota] = useState<TenantQuotaStatus | null>(null);
  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);

  const localIdMapRef = useRef<Map<string, string>>(new Map());

  const refreshLocalLists = useCallback(async () => {
    const [g, u] = await Promise.all([listLocalGuestDocuments(), listLocalUserBackups()]);
    setGuestLocalDocs(g);
    setUserBackups(u);
  }, []);

  useEffect(() => {
    void refreshLocalLists();
  }, [authSession, refreshLocalLists]);

  useEffect(() => {
    if (!loggedIn) {
      setQuota(null);
      return;
    }
    void fetchTenantQuotaStatus().then(setQuota);
  }, [authSession, loggedIn]);

  const pollTaskIds = useCallback(
    async (taskIds: number[]) => {
      let done = false;
      while (!done) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        const latest = await Promise.all(taskIds.map((taskId) => onGetTask(taskId)));
        const avgOverall = latest.reduce((sum, item) => sum + (item.progress_percent || 0), 0) / latest.length;
        const avgExtract = latest.reduce((sum, item) => sum + (item.extract_progress_percent || 0), 0) / latest.length;
        const stageSplit = latest.reduce(
          (sum, item) => {
            const split = splitIndexStage(item.index_progress_percent || 0, item.phase || "", item.status || "");
            return {
              vector: sum.vector + split.vector,
              indexBuild: sum.indexBuild + split.indexBuild,
            };
          },
          { vector: 0, indexBuild: 0 }
        );
        setParseProgress(clampPercent(avgOverall));
        setExtractProgress(clampPercent(avgExtract));
        setVectorProgress(clampPercent(stageSplit.vector / latest.length));
        setIndexBuildProgress(clampPercent(stageSplit.indexBuild / latest.length));
        setSlowHint(latest.some((t) => (t.page_count || 0) > 100 && t.status !== "completed"));

        const phase = latest.find((item) => item.status !== "completed")?.phase || "completed";
        if (phase === "parsing") {
          setPhaseText("正在 OCR / 提取文本…");
        } else if (phase === "splitting") {
          setPhaseText("正在切分文档，准备向量化…");
        } else if (phase === "indexing") {
          setPhaseText(avgOverall < 90 ? "正在向量化并写入知识库…" : "正在建立检索索引…");
        } else if (phase === "completed") {
          setPhaseText("上传、解析、向量化与建索引已完成");
        } else if (phase === "failed") {
          setPhaseText("任务失败");
        } else {
          setPhaseText("任务排队中…");
        }

        const hasFailed = latest.some((item) => item.status === "failed");
        const allDone = latest.every((item) => item.status === "completed");
        if (hasFailed) {
          const failed = latest.find((item) => item.status === "failed");
          throw new Error(failed?.error_message || "上传任务失败");
        }
        done = allDone;
      }
    },
    [onGetTask]
  );

  const getOrCreateLocalId = useCallback((f: File): string => {
    const k = fileKeyForUpload(f);
    let id = localIdMapRef.current.get(k);
    if (!id) {
      id = crypto.randomUUID();
      localIdMapRef.current.set(k, id);
    }
    return id;
  }, []);

  const saveOneFileLocally = useCallback(
    async (file: File): Promise<void> => {
      const id = getOrCreateLocalId(file);
      const token = getAccessToken();
      if (token) {
        await putLocalUserBackup({
          id,
          taskId: 0,
          filename: file.name,
          createdAt: new Date().toISOString(),
          originalBlob: file,
          processJson: localDraftProcessJson(),
        });
      } else {
        await putLocalGuestDocument({
          id,
          filename: file.name,
          size: file.size,
          createdAt: new Date().toISOString(),
          blob: file,
        });
      }
    },
    [getOrCreateLocalId]
  );

  const handleUpload = async () => {
    if (!selectedFiles.length) return;
    setLocalError("");
    setLocalInfo("");

    const failedLocalFiles: File[] = [];
    for (const file of selectedFiles) {
      try {
        await saveOneFileLocally(file);
      } catch {
        failedLocalFiles.push(file);
      }
    }

    const filesToUpload = uploadToCloud ? selectedFiles : failedLocalFiles;

    if (filesToUpload.length === 0) {
      setLocalInfo("已保存到本机（浏览器），未上传云端。");
      await refreshLocalLists();
      setSelectedFiles([]);
      return;
    }

    setUploading(true);
    setUploadProgress(0);
    setParseProgress(0);
    setExtractProgress(0);
    setVectorProgress(0);
    setIndexBuildProgress(0);
    setPhaseText("正在上传文件…");
    setSlowHint(false);

    try {
      const tasks = await onCreateUploadTasks(filesToUpload, "all", "academic", setUploadProgress, {
        ocr_mode: ocrMode,
        external_ocr_confirmed: false,
      });
      if (!tasks.length) {
        throw new Error("未创建上传任务");
      }
      setUploadProgress(100);
      setPhaseText("上传完成，正在解析文档…");
      const taskIds = tasks.map((t) => t.task_id);
      await pollTaskIds(taskIds);
      await mergeCloudIntoLocalBackups(tasks, filesToUpload, localIdMapRef.current);
      await onRefresh();
      await refreshLocalLists();
      setSelectedFiles([]);
      setLocalInfo(uploadToCloud ? "云端解析已完成，本机备份已更新。" : "本机保存失败部分已上传云端并完成解析。");
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const mergeFiles = (incoming: File[]) => {
    const map = new Map<string, File>();
    [...selectedFiles, ...incoming].forEach((f) => map.set(fileKeyForUpload(f), f));
    setSelectedFiles(Array.from(map.values()));
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const files = Array.from(event.dataTransfer.files || []);
    if (files.length) {
      mergeFiles(files);
    }
  };

  const handleDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!dragging) {
      setDragging(true);
    }
  };

  const handleDragLeave = () => {
    setDragging(false);
  };

  const totalMb = selectedFiles.reduce((sum, f) => sum + f.size, 0) / (1024 * 1024);
  const hasChunkSized = selectedFiles.some((f) => f.size >= CHUNK_UPLOAD_THRESHOLD);

  return (
    <section className="space-y-3">
      <div className="card p-4">
        <p className="mb-2 text-xs text-slate-600">
          默认先写入本机（IndexedDB）。勾选「上传云端」后所选文件会参与服务端解析与知识库检索；未勾选时，仅在本机保存失败时自动上传对应文件。
        </p>
        <p className="mb-2 text-xs font-medium text-amber-700">
          未勾选「上传云端」且本机保存成功时，只会保存原件，不会开始云端解析、向量化和建索引。
        </p>
        {authLocalEnabled && (
          <p className="mb-2 text-xs text-violet-700">
            登录后可使用「用户本机备份」库；未登录时使用游客本机存储，数据仅存于当前浏览器。
          </p>
        )}
        <label className="mb-3 flex cursor-pointer items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={uploadToCloud}
            onChange={(e) => setUploadToCloud(e.target.checked)}
          />
          <span>
            <span className="font-semibold text-slate-800">上传云端</span>
            <span className="block text-xs text-slate-500">勾选后全部所选文件上传并解析；不勾选则仅本机失败时自动上传。</span>
          </span>
        </label>
        <div className="mb-3 rounded-2xl bg-white/75 p-3 ring-1 ring-slate-200">
          <p className="text-sm font-semibold text-slate-800">OCR 模式</p>
          <div className="mt-2 space-y-2 text-sm">
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name="ocr-mode"
                className="mt-0.5"
                checked={ocrMode === "standard"}
                onChange={() => setOcrMode("standard")}
              />
              <span>
                <span className="font-medium text-slate-800">默认文字处理</span>
                <span className="block text-xs text-slate-500">
                  优先直接抽取文本；扫描件再走 PaddleOCR，按普通 OCR 次数结算。
                </span>
              </span>
            </label>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name="ocr-mode"
                className="mt-0.5"
                checked={ocrMode === "complex_layout"}
                onChange={() => setOcrMode("complex_layout")}
              />
              <span>
                <span className="font-medium text-slate-800">复杂版式（付费，消耗 OCR token）</span>
                <span className="block text-xs text-slate-500">
                  适合图表多、分栏多、版式复杂的 PDF，直接走外部 GLM-OCR。
                </span>
              </span>
            </label>
          </div>
        </div>
        {quota && (
          <p className="mb-2 text-xs text-slate-500">
            配额：文档 {quota.doc_count}/{quota.max_documents} · 存储{" "}
            {(quota.used_storage_bytes / (1024 * 1024)).toFixed(1)}/
            {(quota.max_storage_bytes / (1024 * 1024)).toFixed(0)} MB
          </p>
        )}

        <div
          className={`relative rounded-3xl border-2 border-dashed p-7 text-center sm:p-12 ${
            dragging
              ? "border-pink-400 bg-gradient-to-br from-pink-50/90 via-violet-50/80 to-teal-50/75"
              : "border-violet-200 bg-gradient-to-br from-white/90 via-violet-50/65 to-pink-50/65"
          }`}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          <input
            type="file"
            multiple
            className="input"
            accept=".pdf,.docx,.pptx,.txt,.md,.markdown,.png,.jpg,.jpeg,.bmp,.tiff,.webp"
            onChange={(e) => mergeFiles(Array.from(e.target.files || []))}
          />
          <p className="mt-3 text-sm font-semibold text-violet-600">拖拽到此处，开始上传与解析</p>
          <p className="mt-1 text-xs text-slate-400">支持 PDF、DOCX、PPTX、TXT、MD、图片等格式</p>
          {hasChunkSized && (
            <p className="mt-1 text-xs text-amber-700">
              单个文件 ≥ {(CHUNK_UPLOAD_THRESHOLD / (1024 * 1024)).toFixed(0)} MB 时将使用分片上传，大文件请耐心等待。
            </p>
          )}
          {selectedFiles.length > 0 && (
            <p className="mt-1 text-xs text-slate-500">
              已选择 {selectedFiles.length} 个文件（合计约 {totalMb.toFixed(2)} MB）
            </p>
          )}
          {slowHint && <p className="mt-1 text-xs text-slate-500">长文档解析可能较久，可先处理其他任务。</p>}
          {ocrMode === "complex_layout" && (
            <p className="mt-1 text-xs text-amber-700">复杂版式将进入付费通道，并按 OCR token 计费。</p>
          )}
        </div>

        {(uploading || uploadProgress > 0 || parseProgress > 0) && (
          <div className="mt-3 space-y-2">
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-slate-600">
                <span>文件上传</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full bg-indigo-600 transition-all" style={{ width: `${uploadProgress}%` }} />
              </div>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-slate-600">
                <span>OCR / 文本提取</span>
                <span>{extractProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full bg-emerald-600 transition-all" style={{ width: `${extractProgress}%` }} />
              </div>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-slate-600">
                <span>向量化</span>
                <span>{vectorProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full bg-cyan-600 transition-all" style={{ width: `${vectorProgress}%` }} />
              </div>
            </div>
            <div>
              <div className="mb-1 flex items-center justify-between text-[11px] text-slate-600">
                <span>建索引</span>
                <span>{indexBuildProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full bg-violet-600 transition-all" style={{ width: `${indexBuildProgress}%` }} />
              </div>
            </div>
            <p className="text-[11px] text-slate-500">云端处理总进度 {parseProgress}%</p>
            {phaseText && <p className="text-xs text-slate-600">{phaseText}</p>}
          </div>
        )}

        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-primary" disabled={loading || uploading || !selectedFiles.length} onClick={() => void handleUpload()}>
            {uploading ? "处理中…" : "开始处理"}
          </button>
          <button
            className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-violet-600 ring-1 ring-violet-200 transition hover:bg-violet-50"
            onClick={onRefresh}
          >
            刷新列表
          </button>
        </div>
        {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
        {localError && <p className="mt-2 text-xs text-rose-600">{localError}</p>}
        {localInfo && <p className="mt-2 text-xs text-emerald-700">{localInfo}</p>}
      </div>

      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-violet-600">云端文档</h3>
        <div className="space-y-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-gradient-to-r from-white to-violet-50/70 px-3 py-2 ring-1 ring-violet-100"
            >
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{doc.filename || doc.title}</p>
                <p className="text-xs text-slate-500">
                  {doc.discipline} · {doc.document_type}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <button
                  type="button"
                  className="rounded-2xl bg-white/90 px-2 py-1.5 text-xs font-medium text-violet-700 ring-1 ring-violet-200 hover:bg-violet-50"
                  onClick={() =>
                    void downloadCloudDocumentOriginal(doc.id, doc.filename || doc.title || `doc-${doc.id}`).catch(
                      (e) => setLocalError(e instanceof Error ? e.message : "下载失败")
                    )
                  }
                >
                  下载原件
                </button>
                <button className="btn-danger text-xs" onClick={() => onDelete(doc.id)}>
                  删除
                </button>
              </div>
            </div>
          ))}
          {documents.length === 0 && <p className="text-xs text-slate-500">暂无云端文档。</p>}
        </div>
      </div>

      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-slate-700">本机副本（仅当前浏览器）</h3>
        {loggedIn ? (
          <div className="space-y-2">
            {userBackups.map((b) => (
              <div
                key={b.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-slate-50 px-3 py-2 ring-1 ring-slate-200"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{b.filename}</p>
                  <p className="text-xs text-slate-500">
                    {b.taskId === 0 ? "仅本机草稿" : `任务 #${b.taskId}`} · {new Date(b.createdAt).toLocaleString()}
                  </p>
                </div>
                <button
                  type="button"
                  className="text-xs text-rose-600 hover:underline"
                  onClick={() => void deleteLocalUserBackup(b.id).then(() => refreshLocalLists())}
                >
                  删除本机
                </button>
              </div>
            ))}
            {userBackups.length === 0 && <p className="text-xs text-slate-500">暂无用户本机备份。</p>}
          </div>
        ) : (
          <div className="space-y-2">
            {guestLocalDocs.map((g) => (
              <div
                key={g.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-slate-50 px-3 py-2 ring-1 ring-slate-200"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{g.filename}</p>
                  <p className="text-xs text-slate-500">
                    游客仅本机 · {(g.size / 1024).toFixed(1)} KB · {new Date(g.createdAt).toLocaleString()}
                  </p>
                </div>
                <button
                  type="button"
                  className="text-xs text-rose-600 hover:underline"
                  onClick={() => void deleteLocalGuestDocument(g.id).then(() => refreshLocalLists())}
                >
                  删除
                </button>
              </div>
            ))}
            {guestLocalDocs.length === 0 && <p className="text-xs text-slate-500">暂无游客本机文件。</p>}
          </div>
        )}
      </div>
    </section>
  );
}

export default UploadTab;
