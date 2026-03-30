import { DragEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DocumentItem,
  UploadTaskItem,
  EXTERNAL_OCR_SIZE_THRESHOLD_BYTES,
  ExternalOcrConfirmRequiredError,
  resumeChunkUploadAfterExternalOcrConfirm,
  getAccessToken,
  fetchTenantQuotaStatus,
  buildLocalProcessSnapshot,
  CHUNK_UPLOAD_THRESHOLD,
  downloadCloudDocumentOriginal,
  fileKeyForUpload,
  type TenantQuotaStatus,
} from "../hooks/useDocuments";
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
import { GPU_OCR_CALL_PACKS } from "../config/gpuOcrPricing";

type Props = {
  documents: DocumentItem[];
  loading: boolean;
  error: string;
  onCreateUploadTasks: (
    files: File[],
    discipline: string,
    documentType: string,
    onUploadProgress?: (percent: number) => void,
    options?: { use_gpu_ocr?: boolean; external_ocr_confirmed?: boolean }
  ) => Promise<UploadTaskItem[]>;
  onGetTask: (taskId: number) => Promise<UploadTaskItem>;
  onDelete: (id: number) => Promise<void>;
  onRefresh: () => Promise<void>;
  authLocalEnabled?: boolean;
  authSession?: number;
};

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
  const [phaseText, setPhaseText] = useState("");
  const [slowHint, setSlowHint] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [localError, setLocalError] = useState("");
  const [localInfo, setLocalInfo] = useState("");
  const [dragging, setDragging] = useState(false);
  const [preUploadLargePdfApproved, setPreUploadLargePdfApproved] = useState(false);
  const [externalOcrModalOpen, setExternalOcrModalOpen] = useState(false);
  const [externalOcrModalMode, setExternalOcrModalMode] = useState<"pre_upload" | "after_chunk" | null>(null);
  const [pendingChunkOcr, setPendingChunkOcr] = useState<ExternalOcrConfirmRequiredError | null>(null);
  const [uploadToCloud, setUploadToCloud] = useState(false);
  const [guestLocalDocs, setGuestLocalDocs] = useState<LocalGuestDoc[]>([]);
  const [userBackups, setUserBackups] = useState<LocalUserBackupRecord[]>([]);
  const [quota, setQuota] = useState<TenantQuotaStatus | null>(null);

  const localIdMapRef = useRef<Map<string, string>>(new Map());
  const filesBackupRef = useRef<File[]>([]);

  const refreshLocalLists = useCallback(async () => {
    const [g, u] = await Promise.all([listLocalGuestDocuments(), listLocalUserBackups()]);
    setGuestLocalDocs(g);
    setUserBackups(u);
  }, []);

  useEffect(() => {
    void refreshLocalLists();
  }, [authSession, refreshLocalLists]);

  useEffect(() => {
    if (!getAccessToken()) {
      setQuota(null);
      return;
    }
    void fetchTenantQuotaStatus().then(setQuota);
  }, [authSession]);

  useEffect(() => {
    setPreUploadLargePdfApproved(false);
  }, [selectedFiles, uploadToCloud]);

  const pricingText = useMemo(() => {
    return GPU_OCR_CALL_PACKS.map(
      (p) => `${p.name}（${p.calls} 次，¥${p.priceCny}，约 ¥${p.pricePerCallCny.toFixed(4)}/次）`
    ).join("；");
  }, []);

  const pollTaskIds = useCallback(
    async (taskIds: number[]) => {
      let done = false;
      while (!done) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        const latest = await Promise.all(taskIds.map((taskId) => onGetTask(taskId)));
        const avg = latest.reduce((sum, item) => sum + (item.progress_percent || 0), 0) / latest.length;
        setParseProgress(Math.max(0, Math.min(100, Math.round(avg))));
        setSlowHint(latest.some((t) => (t.page_count || 0) > 100 && t.status !== "completed"));

        const phase = latest.find((item) => item.status !== "completed")?.phase || "completed";
        if (phase === "parsing") {
          setPhaseText("正在解析文档…");
        } else if (phase === "indexing") {
          setPhaseText("正在建立索引…");
        } else if (phase === "completed") {
          setPhaseText("上传与解析已完成");
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

    const hasLargeInQueue = filesToUpload.some(
      (f) => f.name.toLowerCase().endsWith(".pdf") && f.size > EXTERNAL_OCR_SIZE_THRESHOLD_BYTES
    );
    if (hasLargeInQueue && !preUploadLargePdfApproved) {
      setExternalOcrModalMode("pre_upload");
      setExternalOcrModalOpen(true);
      return;
    }

    setUploading(true);
    setUploadProgress(0);
    setParseProgress(0);
    setPhaseText("正在上传文件…");
    setSlowHint(false);
    filesBackupRef.current = filesToUpload.slice();

    try {
      const externalOcrConfirmed = !hasLargeInQueue || preUploadLargePdfApproved;
      const tasks = await onCreateUploadTasks(filesToUpload, "all", "academic", setUploadProgress, {
        use_gpu_ocr: false,
        external_ocr_confirmed: externalOcrConfirmed,
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
      setPreUploadLargePdfApproved(false);
      setLocalInfo(uploadToCloud ? "云端解析已完成，本机备份已更新。" : "本机保存失败部分已上传云端并完成解析。");
    } catch (e) {
      if (e instanceof ExternalOcrConfirmRequiredError) {
        setPendingChunkOcr(e);
        setExternalOcrModalMode("after_chunk");
        setExternalOcrModalOpen(true);
      } else {
        setLocalError(e instanceof Error ? e.message : "上传失败");
      }
    } finally {
      setUploading(false);
    }
  };

  const handleExternalOcrModalContinue = async () => {
    if (externalOcrModalMode === "pre_upload") {
      setPreUploadLargePdfApproved(true);
      setExternalOcrModalOpen(false);
      setExternalOcrModalMode(null);
      await handleUpload();
      return;
    }
    if (externalOcrModalMode === "after_chunk" && pendingChunkOcr) {
      const p = pendingChunkOcr;
      setPendingChunkOcr(null);
      setExternalOcrModalOpen(false);
      setExternalOcrModalMode(null);
      setUploading(true);
      setUploadProgress(90);
      setPhaseText("正在完成上传并解析文档…");
      try {
        const task = await resumeChunkUploadAfterExternalOcrConfirm(
          p.uploadId,
          p.resumeContext.discipline,
          p.resumeContext.documentType,
          p.resumeContext.useGpuOcr
        );
        setUploadProgress(100);
        await pollTaskIds([task.task_id]);
        const file = filesBackupRef.current.find((f) => fileKeyForUpload(f) === p.fileKey);
        if (file) {
          await mergeCloudIntoLocalBackups([task], [file], localIdMapRef.current);
        }
        await onRefresh();
        await refreshLocalLists();
        setSelectedFiles([]);
        setPreUploadLargePdfApproved(false);
        setLocalInfo("云端解析已完成，本机备份已更新。");
      } catch (err) {
        setLocalError(err instanceof Error ? err.message : "上传失败");
      } finally {
        setUploading(false);
      }
    }
  };

  const handleExternalOcrModalCancel = () => {
    setExternalOcrModalOpen(false);
    setExternalOcrModalMode(null);
    setPendingChunkOcr(null);
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
          {selectedFiles.some(
            (f) => f.name.toLowerCase().endsWith(".pdf") && f.size > EXTERNAL_OCR_SIZE_THRESHOLD_BYTES
          ) && (
            <p className="mt-1 text-xs text-slate-500">
              已选择超过 10MB 的 PDF；若为扫描件将提示是否调用外部 OCR（消耗额度）。套餐参考：{pricingText}
            </p>
          )}
        </div>

        {externalOcrModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
            <div className="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
              <p className="text-sm font-semibold text-slate-800">
                此为扫描件或超大 PDF，因处理器受限，超过 10MB 的需要调用外部 OCR，是否继续？
              </p>
              <p className="mt-1 text-xs text-slate-500">
                继续将按扫描页数扣减外部 OCR 次数余额（特殊用户可能不限）。套餐参考：{pricingText}
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  className="btn-primary"
                  onClick={() => void handleExternalOcrModalContinue()}
                  disabled={uploading}
                >
                  继续
                </button>
                <button
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={handleExternalOcrModalCancel}
                  disabled={uploading}
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        )}

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
                <span>解析与入库</span>
                <span>{parseProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-slate-200">
                <div className="h-full bg-emerald-600 transition-all" style={{ width: `${parseProgress}%` }} />
              </div>
            </div>
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
        {getAccessToken() ? (
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
