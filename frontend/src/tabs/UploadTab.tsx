import { DragEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  fetchTenantQuotaStatus,
  buildLocalProcessSnapshot,
  CHUNK_UPLOAD_THRESHOLD,
  fileKeyForUpload,
  type OcrMode,
  type OcrEngineOverride,
  type TenantQuotaStatus,
  downloadCloudDocumentOriginal,
} from "../hooks/useDocuments";
import { getAccessToken, useAccessToken } from "../lib/auth";
import { type EmbeddingMode, useEmbeddingModePreference } from "../lib/embeddingMode";
import ModalShell from "../components/ModalShell";
import {
  deleteClientGuestDocument,
  listClientGuestDocuments,
  listClientUserBackups,
  deleteClientUserBackup,
  putClientUserBackup,
  putClientGuestDocument,
  getClientPersistenceInfo,
} from "../lib/clientPersistence";
import { localDraftProcessJson, type LocalUserBackupRecord } from "../lib/localUserBackup";
import type { LocalGuestDoc } from "../lib/localGuestDocuments";
import { 
  CloudUpload, Sparkles, Upload, FileText, Trash2, Eye, Download, RefreshCw, 
  Search, Filter, CheckSquare, Square, Check, X, AlertCircle, Clock, 
  BarChart, HardDrive, ChevronDown, ChevronUp, Calendar, FileType, 
  FileCode, FileImage, FileArchive, CheckCheck
} from "lucide-react";

type DocumentItem = {
  id: number;
  filename: string;
  title: string;
  created_at: string;
  file_type?: string;
  file_size_bytes?: number;
  status?: "processing" | "completed" | "error" | "queued";
  processing_progress?: number;
};

type UploadTaskItem = {
  task_id: number;
  status: string;
  phase: string;
  progress_percent?: number;
  extract_progress_percent?: number;
  index_progress_percent?: number;
  page_count?: number;
  error_message?: string;
};

type Props = {
  documents: DocumentItem[];
  loading: boolean;
  error: string;
  onCreateUploadTasks: (
    files: File[],
    discipline: string,
    documentType: string,
    onUploadProgress?: (percent: number) => void,
    options?: {
      ocr_mode?: OcrMode;
      ocr_engine_override?: OcrEngineOverride;
      external_ocr_confirmed?: boolean;
      embedding_mode?: EmbeddingMode;
    }
  ) => Promise<UploadTaskItem[]>;
  onGetTask: (taskId: number) => Promise<UploadTaskItem>;
  onDelete: (id: number) => Promise<void>;
  onRefresh: () => Promise<void>;
  authLocalEnabled?: boolean;
  authSession?: number;
  authReady?: boolean;
};

type UploadOcrPromptKind = "complex_layout" | "large_scan";

type UploadOcrPromptState = {
  kind: UploadOcrPromptKind;
  filenames: string[];
  totalPdfCount: number;
  maxFileSizeMb: number;
};

const OCR_SCAN_PROMPT_THRESHOLD_BYTES = 12 * 1024 * 1024;
const COMPLEX_LAYOUT_FILENAME_HINTS = [
  "scan",
  "scanned",
  "扫描",
  "拍照",
  "试卷",
  "答题",
  "表格",
  "图表",
  "年报",
  "财报",
  "报告",
  "手册",
  "课件",
  "ppt",
  "slide",
  "目录",
  "book",
  "manual",
  "brochure",
  "magazine",
];

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

function isPdfFile(file: File): boolean {
  return file.name.toLowerCase().endsWith(".pdf");
}

function isLikelyComplexLayoutPdf(file: File): boolean {
  if (!isPdfFile(file)) return false;
  const lower = file.name.toLowerCase();
  return COMPLEX_LAYOUT_FILENAME_HINTS.some((keyword) => lower.includes(keyword));
}

function buildUploadOcrPrompt(files: File[]): UploadOcrPromptState | null {
  const pdfs = files.filter(isPdfFile);
  if (!pdfs.length) return null;

  const largeScanCandidates = pdfs.filter((file) => file.size >= OCR_SCAN_PROMPT_THRESHOLD_BYTES);
  if (largeScanCandidates.length) {
    return {
      kind: "large_scan",
      filenames: largeScanCandidates.map((file) => file.name),
      totalPdfCount: pdfs.length,
      maxFileSizeMb: Math.max(...largeScanCandidates.map((file) => file.size / (1024 * 1024))),
    };
  }

  const complexLayoutCandidates = pdfs.filter(isLikelyComplexLayoutPdf);
  if (!complexLayoutCandidates.length) return null;

  return {
    kind: "complex_layout",
    filenames: complexLayoutCandidates.map((file) => file.name),
    totalPdfCount: pdfs.length,
    maxFileSizeMb: Math.max(...complexLayoutCandidates.map((file) => file.size / (1024 * 1024))),
  };
}

function formatPromptFileNames(filenames: string[]): string {
  if (filenames.length <= 2) return filenames.join(" / ");
  return `${filenames.slice(0, 2).join(" / ")} +${filenames.length - 2}`;
}

function isApiOcrEngine(engine: OcrEngineOverride): boolean {
  return engine === "baidu" || engine === "glm-ocr";
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
    await putClientUserBackup({
      id: localId,
      taskId: task.task_id,
      filename: file.name,
      createdAt: new Date().toISOString(),
      originalBlob: file,
      processJson: JSON.stringify(snapshot),
    });
  }
}

async function attachTaskIdsToLocalBackups(
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
    await putClientUserBackup({
      id: localId,
      taskId: task.task_id,
      filename: file.name,
      createdAt: new Date().toISOString(),
      originalBlob: file,
      processJson: localDraftProcessJson(),
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
  authSession = 0,
  authReady = true,
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
  const [ocrMode] = useState<OcrMode>("standard");
  const [ocrEngineChoice, setOcrEngineChoice] = useState<OcrEngineOverride>("local");
  const [embeddingMode, setEmbeddingMode] = useEmbeddingModePreference();
  const [uploadToCloud] = useState(true);
  const [, setGuestLocalDocs] = useState<LocalGuestDoc[]>([]);
  const [, setUserBackups] = useState<LocalUserBackupRecord[]>([]);
  const [quota, setQuota] = useState<TenantQuotaStatus | null>(null);
  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);
  const storageInfo = getClientPersistenceInfo();
  const localStorageName = storageInfo.storageTarget === "device-native" ? "Native" : "Browser";
  
  // 新增状态
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "processing" | "completed" | "error" | "queued">("all");
  const [typeFilter, setTypeFilter] = useState<"all" | "pdf" | "image" | "document" | "other">("all");
  const [dateFilter, setDateFilter] = useState<"all" | "week" | "month" | "year">("all");
  const [selectedDocs, setSelectedDocs] = useState<number[]>([]);
  const [showAdvancedFilters, setShowAdvancedFilters] = useState(false);
  const [realTimeMessages, setRealTimeMessages] = useState<{id: string, message: string, type: "info" | "success" | "error", timestamp: string}[]>([]);
  const [ocrPrompt, setOcrPrompt] = useState<UploadOcrPromptState | null>(null);

  const localIdMapRef = useRef<Map<string, string>>(new Map());
  const ocrPromptResolverRef = useRef<((choice: OcrEngineOverride | null) => void) | null>(null);

  // 工具函数
  const getFileTypeIcon = (filename: string) => {
    const ext = filename.split('.').pop()?.toLowerCase();
    if (ext === 'pdf') return <FileText className="text-red-500" size={14} />;
    if (['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'].includes(ext || '')) return <FileImage className="text-pink-500" size={14} />;
    if (['doc', 'docx', 'txt', 'md'].includes(ext || '')) return <FileCode className="text-blue-500" size={14} />;
    return <FileArchive className="text-slate-500" size={14} />;
  };

  const getStatusBadgeColor = (status?: string) => {
    switch (status) {
      case 'processing': return 'bg-yellow-400 text-slate-900';
      case 'completed': return 'bg-green-400 text-slate-900';
      case 'error': return 'bg-red-400 text-white';
      case 'queued': return 'bg-slate-400 text-white';
      default: return 'bg-slate-200 text-slate-700';
    }
  };

  const getStatusText = (status?: string) => {
    switch (status) {
      case 'processing': return '处理中';
      case 'completed': return '已完成';
      case 'error': return '错误';
      case 'queued': return '排队中';
      default: return '未知';
    }
  };

  const formatFileSize = (bytes?: number) => {
    if (!bytes) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let size = bytes;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex++;
    }
    return `${size.toFixed(1)} ${units[unitIndex]}`;
  };

  const finishOcrPrompt = useCallback((choice: OcrEngineOverride | null) => {
    setOcrPrompt(null);
    const resolver = ocrPromptResolverRef.current;
    ocrPromptResolverRef.current = null;
    resolver?.(choice);
  }, []);

  const requestOcrChoice = useCallback((files: File[], preferredEngine: OcrEngineOverride): Promise<OcrEngineOverride | null> => {
    if (preferredEngine !== "local") return Promise.resolve(preferredEngine);
    const prompt = buildUploadOcrPrompt(files);
    if (!prompt) return Promise.resolve("local");
    return new Promise((resolve) => {
      ocrPromptResolverRef.current = resolve;
      setOcrPrompt(prompt);
    });
  }, []);

  const addRealTimeMessage = (message: string, type: "info" | "success" | "error" = "info") => {
    const id = Date.now().toString();
    const timestamp = new Date().toLocaleTimeString();
    setRealTimeMessages(prev => [...prev, { id, message, type, timestamp }]);
    
    // 自动移除消息
    setTimeout(() => {
      setRealTimeMessages(prev => prev.filter(msg => msg.id !== id));
    }, 5000);
  };

  const filteredDocuments = documents.filter(doc => {
    // 搜索过滤
    if (searchQuery && !doc.filename.toLowerCase().includes(searchQuery.toLowerCase()) && 
        !doc.title.toLowerCase().includes(searchQuery.toLowerCase())) {
      return false;
    }
    
    // 状态过滤
    if (statusFilter !== 'all' && doc.status !== statusFilter) {
      return false;
    }
    
    // 类型过滤
    if (typeFilter !== 'all') {
      const ext = doc.filename.split('.').pop()?.toLowerCase();
      if (typeFilter === 'pdf' && ext !== 'pdf') return false;
      if (typeFilter === 'image' && !['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp'].includes(ext || '')) return false;
      if (typeFilter === 'document' && !['doc', 'docx', 'txt', 'md'].includes(ext || '')) return false;
      if (typeFilter === 'other' && ['pdf', 'jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'doc', 'docx', 'txt', 'md'].includes(ext || '')) return false;
    }
    
    // 日期过滤
    if (dateFilter !== 'all') {
      const docDate = new Date(doc.created_at);
      const now = new Date();
      const diffDays = Math.floor((now.getTime() - docDate.getTime()) / (1000 * 60 * 60 * 24));
      
      if (dateFilter === 'week' && diffDays > 7) return false;
      if (dateFilter === 'month' && diffDays > 30) return false;
      if (dateFilter === 'year' && diffDays > 365) return false;
    }
    
    return true;
  });

  const handleSelectAll = () => {
    if (selectedDocs.length === filteredDocuments.length) {
      setSelectedDocs([]);
    } else {
      setSelectedDocs(filteredDocuments.map(doc => doc.id));
    }
  };

  const handleDocSelect = (docId: number) => {
    setSelectedDocs(prev => 
      prev.includes(docId) 
        ? prev.filter(id => id !== docId)
        : [...prev, docId]
    );
  };

  const handleBatchDelete = async () => {
    if (!selectedDocs.length) return;
    if (!window.confirm(`确定要删除选中的 ${selectedDocs.length} 个文档吗？`)) return;
    
    try {
      const docsToDelete = documents.filter((doc) => selectedDocs.includes(doc.id));
      for (const docId of selectedDocs) {
        await onDelete(docId);
      }
      await Promise.all(docsToDelete.map((doc) => cleanupLocalBackupsForDocument(doc)));
      setSelectedDocs([]);
      addRealTimeMessage(`成功删除 ${selectedDocs.length} 个文档`, "success");
      await onRefresh();
      await refreshLocalLists();
    } catch (e) {
      addRealTimeMessage(`删除失败: ${e instanceof Error ? e.message : '未知错误'}`, "error");
    }
  };

  const handleDownloadDoc = async (docId: number, filename: string) => {
    try {
      addRealTimeMessage(`正在下载 ${filename}...`, "info");
      await downloadCloudDocumentOriginal(docId, filename);
      addRealTimeMessage(`${filename} 下载完成`, "success");
    } catch (e) {
      addRealTimeMessage(`下载失败: ${e instanceof Error ? e.message : '未知错误'}`, "error");
    }
  };

  const handleReprocessDoc = (docId: number) => {
    addRealTimeMessage(`文档 ${docId} 重新处理请求已发送`, "info");
    // TODO: 实现重新处理逻辑
  };

  const refreshLocalLists = useCallback(async () => {
    const [g, u] = await Promise.all([listClientGuestDocuments(), listClientUserBackups()]);
    setGuestLocalDocs(g);
    setUserBackups(u);
  }, []);

  const cleanupLocalBackupsForDocument = useCallback(async (doc: DocumentItem) => {
    const backups = await listClientUserBackups();
    const matched = backups.filter((backup) => {
      if ((backup.filename || "").trim() === (doc.filename || "").trim()) return true;
      try {
        const parsed = JSON.parse(backup.processJson || "{}") as {
          document?: { id?: unknown; filename?: unknown } | null;
          task?: { document_id?: unknown; filename?: unknown } | null;
        };
        const parsedDocId = Number(parsed?.document?.id ?? parsed?.task?.document_id ?? 0);
        if (parsedDocId > 0 && parsedDocId === doc.id) return true;
        const parsedFilename = String(parsed?.document?.filename || parsed?.task?.filename || "").trim();
        return Boolean(parsedFilename) && parsedFilename === (doc.filename || "").trim();
      } catch {
        return false;
      }
    });
    await Promise.all(matched.map((backup) => deleteClientUserBackup(backup.id)));
  }, []);

  const handleClearLocalProcessCache = useCallback(async () => {
    if (!window.confirm("确定要清空本机过程缓存吗？这只会删除当前设备里的本地备份和中断过程数据，不会删除云端文档。")) {
      return;
    }
    try {
      const [guestDocs, userBackups] = await Promise.all([
        listClientGuestDocuments(),
        listClientUserBackups(),
      ]);
      await Promise.all([
        ...guestDocs.map((doc) => deleteClientGuestDocument(doc.id)),
        ...userBackups.map((backup) => deleteClientUserBackup(backup.id)),
      ]);
      localIdMapRef.current.clear();
      await refreshLocalLists();
      addRealTimeMessage("本机过程缓存已清空", "success");
    } catch (e) {
      addRealTimeMessage(`清空本机缓存失败: ${e instanceof Error ? e.message : "未知错误"}`, "error");
    }
  }, [refreshLocalLists]);

  useEffect(() => {
    void refreshLocalLists();
  }, [authSession, refreshLocalLists]);

  useEffect(() => {
    if (!authReady || !loggedIn) {
      setQuota(null);
      return;
    }
    void fetchTenantQuotaStatus().then(setQuota);
  }, [authReady, authSession, loggedIn]);

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
          setPhaseText("OCR / 文本提取中...");
        } else if (phase === "splitting") {
          setPhaseText("文档分割中...");
        } else if (phase === "indexing") {
          setPhaseText(avgOverall < 90 ? "向量化与存储..." : "构建索引...");
        } else if (phase === "completed") {
          setPhaseText("处理完成");
        } else if (phase === "failed") {
          setPhaseText("任务失败");
        } else {
          setPhaseText("等待队列中...");
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
        await putClientUserBackup({
          id,
          taskId: 0,
          filename: file.name,
          createdAt: new Date().toISOString(),
          originalBlob: file,
          processJson: localDraftProcessJson(),
        });
      } else {
        await putClientGuestDocument({
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
      setLocalInfo(`Saved to ${localStorageName}.`);
      await refreshLocalLists();
      setSelectedFiles([]);
      return;
    }

    const selectedOcrEngine = await requestOcrChoice(filesToUpload, ocrEngineChoice);
    if (!selectedOcrEngine) return;
    const selectedOcrMode: OcrMode = selectedOcrEngine === "glm-ocr" ? "complex_layout" : ocrMode;

    setUploading(true);
    setUploadProgress(0);
    setParseProgress(0);
    setExtractProgress(0);
    setVectorProgress(0);
    setIndexBuildProgress(0);
    setPhaseText("Uploading files...");
    setSlowHint(false);

    try {
      const tasks = await onCreateUploadTasks(filesToUpload, "all", "academic", setUploadProgress, {
        embedding_mode: embeddingMode,
        ocr_mode: selectedOcrMode,
        ocr_engine_override: selectedOcrEngine,
        external_ocr_confirmed: isApiOcrEngine(selectedOcrEngine),
      });
      if (!tasks.length) {
        throw new Error("未创建上传任务");
      }
      setUploadProgress(100);
      setPhaseText("上传完成，解析中...");
      await attachTaskIdsToLocalBackups(tasks, filesToUpload, localIdMapRef.current);
      const taskIds = tasks.map((t) => t.task_id);
      await pollTaskIds(taskIds);
      await mergeCloudIntoLocalBackups(tasks, filesToUpload, localIdMapRef.current);
      await onRefresh();
      await refreshLocalLists();
      setSelectedFiles([]);
      setLocalInfo("知识库更新成功！");
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
    if (!dragging) setDragging(true);
  };

  const handleDragLeave = () => setDragging(false);

  const totalMb = selectedFiles.reduce((sum, f) => sum + f.size, 0) / (1024 * 1024);
  const progress = Math.max(uploadProgress, parseProgress);

  // 存储容量计算
  const storageUsedPercent = quota ? (quota.used_storage_bytes / Math.max(quota.max_storage_bytes, 1)) * 100 : 0;
  const storageColor = storageUsedPercent < 80 ? 'bg-green-400' : storageUsedPercent < 95 ? 'bg-yellow-400' : 'bg-red-400';

  return (
    <div className="flex flex-col gap-8">
      {/* Real-time Messages */}
      {realTimeMessages.length > 0 && (
        <div className="fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-md">
          {realTimeMessages.map(msg => (
            <div 
              key={msg.id} 
              className={`neo-box p-4 text-sm font-black uppercase animate-fadeIn ${msg.type === 'success' ? 'bg-green-400' : msg.type === 'error' ? 'bg-red-400' : 'bg-blue-400'}`}
            >
              <div className="flex justify-between items-center">
                <span>{msg.message}</span>
                <span className="text-xs opacity-60">{msg.timestamp}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Action Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b-4 border-slate-900 pb-6">
        <div className="flex items-center gap-4">
          <button 
            onClick={() => void onRefresh()}
            className="neo-button-sm bg-yellow-400 flex items-center gap-2"
            disabled={loading}
          >
            <Sparkles size={16} />
            刷新列表
          </button>
          <div className="h-8 w-1 bg-slate-900" />
          <p className="text-xs font-black uppercase tracking-widest">
            文档: <span className="text-pink-500">{filteredDocuments.length}</span> / {documents.length}
          </p>
          <div className="h-8 w-1 bg-slate-900" />
          <div className="flex items-center gap-2">
            <HardDrive size={14} className="text-blue-500" />
            <span className="text-xs font-black uppercase">
              存储: {formatFileSize(quota?.used_storage_bytes)} / {formatFileSize(quota?.max_storage_bytes)}
            </span>
          </div>
        </div>
        
        <div className="flex gap-2">
          <button
            className="neo-button-sm bg-white hover:bg-slate-900 hover:text-white flex items-center gap-2"
            onClick={() => void handleClearLocalProcessCache()}
          >
            <Trash2 size={16} />
            清空本机缓存
          </button>
          {selectedDocs.length > 0 && (
            <>
              <button 
                className="neo-button-sm bg-red-400 text-white flex items-center gap-2"
                onClick={handleBatchDelete}
              >
                <Trash2 size={16} />
                删除选中 ({selectedDocs.length})
              </button>
              <div className="h-8 w-1 bg-slate-900" />
            </>
          )}
          <button 
            className="neo-button-sm bg-white hover:bg-red-400 hover:text-white flex items-center gap-2"
            onClick={() => {
              if (window.confirm(`确定要删除所有 ${documents.length} 个文档吗？`)) {
                documents.forEach(doc => onDelete(doc.id));
              }
            }}
           >
             <Trash2 size={16} />
             全部清除
           </button>
        </div>
      </div>

      {/* Search & Filter Bar */}
      <div className="neo-box bg-white p-4">
        <div className="flex flex-col gap-4">
          {/* Main Search */}
          <div className="flex gap-2">
            <div className="flex-1 relative">
              <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-slate-400" size={18} />
              <input
                type="text"
                placeholder="搜索文档名称或标题..."
                className="neo-input pl-10 w-full"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>
            <button 
              className="neo-button-sm bg-slate-100 flex items-center gap-2"
              onClick={() => setShowAdvancedFilters(!showAdvancedFilters)}
            >
              <Filter size={16} />
              筛选 {showAdvancedFilters ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
          </div>

          {/* Advanced Filters */}
          {showAdvancedFilters && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-4 border-t-2 border-slate-100">
              {/* Status Filter */}
              <div>
                <label className="block text-xs font-black uppercase mb-2">状态</label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { value: 'all', label: '全部', color: 'bg-slate-200' },
                    { value: 'processing', label: '处理中', color: 'bg-yellow-400' },
                    { value: 'completed', label: '已完成', color: 'bg-green-400' },
                    { value: 'error', label: '错误', color: 'bg-red-400' },
                    { value: 'queued', label: '排队中', color: 'bg-slate-400' }
                  ].map(option => (
                    <button
                      key={option.value}
                      className={`neo-button-xs ${option.color} ${statusFilter === option.value ? 'border-2 border-slate-900' : ''}`}
                      onClick={() => setStatusFilter(option.value as any)}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Type Filter */}
              <div>
                <label className="block text-xs font-black uppercase mb-2">文件类型</label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { value: 'all', label: '全部', icon: <FileType size={12} /> },
                    { value: 'pdf', label: 'PDF', icon: <FileText size={12} /> },
                    { value: 'image', label: '图片', icon: <FileImage size={12} /> },
                    { value: 'document', label: '文档', icon: <FileCode size={12} /> },
                    { value: 'other', label: '其他', icon: <FileArchive size={12} /> }
                  ].map(option => (
                    <button
                      key={option.value}
                      className={`neo-button-xs bg-white flex items-center gap-1 ${typeFilter === option.value ? 'border-2 border-slate-900' : ''}`}
                      onClick={() => setTypeFilter(option.value as any)}
                    >
                      {option.icon}
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Date Filter */}
              <div>
                <label className="block text-xs font-black uppercase mb-2">上传时间</label>
                <div className="flex flex-wrap gap-2">
                  {[
                    { value: 'all', label: '全部时间', icon: <Calendar size={12} /> },
                    { value: 'week', label: '最近7天', icon: <Calendar size={12} /> },
                    { value: 'month', label: '最近30天', icon: <Calendar size={12} /> },
                    { value: 'year', label: '最近1年', icon: <Calendar size={12} /> }
                  ].map(option => (
                    <button
                      key={option.value}
                      className={`neo-button-xs bg-white flex items-center gap-1 ${dateFilter === option.value ? 'border-2 border-slate-900' : ''}`}
                      onClick={() => setDateFilter(option.value as any)}
                    >
                      {option.icon}
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Storage Capacity Dashboard */}
      <div className="neo-box bg-white p-6">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-sm font-black uppercase flex items-center gap-2">
            <HardDrive size={16} className="text-blue-500" />
            存储容量
          </h3>
          <div className="flex items-center gap-2">
            <span className="text-xs font-black text-slate-600">
              {storageUsedPercent.toFixed(1)}% 已用
            </span>
            {storageUsedPercent >= 95 && (
              <AlertCircle size={14} className="text-red-500 animate-pulse" />
            )}
          </div>
        </div>
        
        <div className="space-y-4">
          {/* Storage Progress Bar */}
          <div className="neo-box-sm bg-slate-100 h-6 overflow-hidden relative">
            <div 
              className={`h-full ${storageColor} transition-all duration-500 ease-out flex items-center justify-end px-2`}
              style={{ width: `${Math.min(storageUsedPercent, 100)}%` }}
            >
              {storageUsedPercent > 20 && (
                <span className="text-xs font-black text-slate-900">
                  {formatFileSize(quota?.used_storage_bytes)}
                </span>
              )}
            </div>
            {storageUsedPercent <= 20 && (
              <span className="absolute right-2 top-1/2 transform -translate-y-1/2 text-xs font-black text-slate-600">
                {formatFileSize(quota?.used_storage_bytes)}
              </span>
            )}
          </div>
          
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="neo-box-sm bg-yellow-50 p-3">
              <p className="text-[10px] font-black uppercase opacity-60">文档数量</p>
              <p className="text-lg font-black">{quota?.doc_count || 0} / {quota?.max_documents || '∞'}</p>
            </div>
            <div className="neo-box-sm bg-blue-50 p-3">
              <p className="text-[10px] font-black uppercase opacity-60">向量数量</p>
              <p className="text-lg font-black">{quota?.vector_count || 0} / {quota?.max_vectors || '∞'}</p>
            </div>
            <div className="neo-box-sm bg-green-50 p-3">
              <p className="text-[10px] font-black uppercase opacity-60">存储使用</p>
              <p className="text-lg font-black">{formatFileSize(quota?.used_storage_bytes)}</p>
            </div>
            <div className="neo-box-sm bg-pink-50 p-3">
              <p className="text-[10px] font-black uppercase opacity-60">剩余空间</p>
              <p className="text-lg font-black">{formatFileSize(Math.max(0, (quota?.max_storage_bytes || 0) - (quota?.used_storage_bytes || 0)))}</p>
            </div>
          </div>
          
          <div className="flex gap-2">
            <button 
              className="neo-button-xs bg-green-400 text-slate-900 flex items-center gap-1"
              onClick={() => addRealTimeMessage("正在清理临时文件...", "info")}
            >
              <Trash2 size={12} />
              释放空间
            </button>
          </div>
        </div>
      </div>

      {/* Documents List Table */}
      <div className="neo-box bg-white p-0 overflow-hidden">
        <div className="p-4 border-b-4 border-slate-900 flex justify-between items-center">
          <h3 className="text-sm font-black uppercase flex items-center gap-2">
            <FileText size={16} className="text-blue-500" />
            文档列表 ({filteredDocuments.length})
          </h3>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <button
                onClick={handleSelectAll}
                className="text-slate-600 hover:text-slate-900"
              >
                {selectedDocs.length === filteredDocuments.length && filteredDocuments.length > 0 ? 
                  <CheckSquare size={16} className="text-green-500" /> : 
                  <Square size={16} />
                }
              </button>
              <span className="text-xs font-black uppercase">
                {selectedDocs.length > 0 ? `已选 ${selectedDocs.length} 个` : '选择'}
              </span>
            </div>
            {selectedDocs.length > 0 && (
              <div className="flex gap-2">
                <button 
                  className="neo-button-xs bg-blue-400 text-white flex items-center gap-1"
                  onClick={() => addRealTimeMessage("批量导出功能开发中...", "info")}
                >
                  <Download size={12} />
                  导出选中
                </button>
                <button 
                  className="neo-button-xs bg-yellow-400 text-slate-900 flex items-center gap-1"
                  onClick={() => addRealTimeMessage("批量重新处理功能开发中...", "info")}
                >
                  <RefreshCw size={12} />
                  重新处理
                </button>
              </div>
            )}
          </div>
        </div>

        {loading ? (
          <div className="p-8 text-center">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-4 border-slate-900 border-t-transparent"></div>
            <p className="mt-4 text-sm font-black uppercase">加载中...</p>
          </div>
        ) : filteredDocuments.length === 0 ? (
          <div className="p-8 text-center">
            <FileText size={48} className="mx-auto text-slate-300 mb-4" />
            <p className="text-sm font-black uppercase">暂无文档</p>
            <p className="text-xs text-slate-500 mt-2">上传一些文件开始使用</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-50 border-b-2 border-slate-200">
                <tr>
                  <th className="p-3 text-left">
                    <button
                      onClick={handleSelectAll}
                      className="flex items-center gap-2 text-xs font-black uppercase"
                    >
                      {selectedDocs.length === filteredDocuments.length && filteredDocuments.length > 0 ? 
                        <CheckSquare size={14} className="text-green-500" /> : 
                        <Square size={14} />
                      }
                      选择
                    </button>
                  </th>
                  <th className="p-3 text-left text-xs font-black uppercase">文件</th>
                  <th className="p-3 text-left text-xs font-black uppercase">状态</th>
                  <th className="p-3 text-left text-xs font-black uppercase">大小</th>
                  <th className="p-3 text-left text-xs font-black uppercase">上传时间</th>
                  <th className="p-3 text-left text-xs font-black uppercase">操作</th>
                </tr>
              </thead>
              <tbody>
                {filteredDocuments.map((doc) => (
                  <tr 
                    key={doc.id} 
                    className={`border-b border-slate-100 hover:bg-slate-50 ${selectedDocs.includes(doc.id) ? 'bg-blue-50' : ''}`}
                  >
                    <td className="p-3">
                      <button
                        onClick={() => handleDocSelect(doc.id)}
                        className="text-slate-600 hover:text-slate-900"
                      >
                        {selectedDocs.includes(doc.id) ? 
                          <CheckSquare size={16} className="text-green-500" /> : 
                          <Square size={16} />
                        }
                      </button>
                    </td>
                    <td className="p-3">
                      <div className="flex items-center gap-2">
                        {getFileTypeIcon(doc.filename)}
                        <div className="min-w-0">
                          <p className="text-sm font-bold truncate max-w-[200px]" title={doc.filename}>
                            {doc.filename}
                          </p>
                          <p className="text-xs text-slate-500 truncate max-w-[200px]" title={doc.title}>
                            {doc.title || '无标题'}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="p-3">
                      <span className={`neo-badge-xs ${getStatusBadgeColor(doc.status)}`}>
                        {getStatusText(doc.status)}
                        {doc.status === 'processing' && doc.processing_progress && (
                          <span className="ml-1">({doc.processing_progress}%)</span>
                        )}
                      </span>
                    </td>
                    <td className="p-3 text-xs font-black">
                      {formatFileSize(doc.file_size_bytes)}
                    </td>
                    <td className="p-3 text-xs font-black">
                      {new Date(doc.created_at).toLocaleDateString()}
                      <br />
                      <span className="text-slate-500 text-[10px]">
                        {new Date(doc.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
                      </span>
                    </td>
                    <td className="p-3">
                      <div className="flex gap-1">
                        <button
                          onClick={() => addRealTimeMessage(`查看文档 ${doc.filename}`, "info")}
                          className="neo-button-icon bg-blue-100 text-blue-600 hover:bg-blue-200"
                          title="查看"
                        >
                          <Eye size={14} />
                        </button>
                        <button
                          onClick={() => handleDownloadDoc(doc.id, doc.filename)}
                          className="neo-button-icon bg-green-100 text-green-600 hover:bg-green-200"
                          title="下载"
                        >
                          <Download size={14} />
                        </button>
                        <button
                          onClick={() => handleReprocessDoc(doc.id)}
                          className="neo-button-icon bg-yellow-100 text-yellow-600 hover:bg-yellow-200"
                          title="重新处理"
                        >
                          <RefreshCw size={14} />
                        </button>
                        <button
                          onClick={async () => {
                            if (window.confirm(`确定要删除 ${doc.filename} 吗？`)) {
                              await onDelete(doc.id);
                              await cleanupLocalBackupsForDocument(doc);
                              await refreshLocalLists();
                              addRealTimeMessage(`已删除 ${doc.filename}`, "success");
                            }
                          }}
                          className="neo-button-icon bg-red-100 text-red-600 hover:bg-red-200"
                          title="删除"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {filteredDocuments.length > 0 && (
          <div className="p-4 border-t-2 border-slate-100 flex justify-between items-center text-xs font-black uppercase">
            <div className="flex items-center gap-4">
              <span>显示 {filteredDocuments.length} 个文档</span>
              {selectedDocs.length > 0 && (
                <span className="text-blue-600">已选 {selectedDocs.length} 个</span>
              )}
            </div>
            <div className="flex gap-2">
              <button 
                className="neo-button-xs bg-white flex items-center gap-1"
                onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
              >
                <ChevronUp size={12} />
                回到顶部
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Dropzone */}
      <div
        className={`neo-box min-h-[200px] flex flex-col items-center justify-center p-8 transition-all cursor-pointer ${
          dragging ? "bg-yellow-100 scale-[1.02]" : "bg-white hover:bg-slate-50"
        } relative`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        <input
          type="file"
          multiple
          className="absolute inset-0 z-10 cursor-pointer opacity-0"
          accept=".pdf,.docx,.pptx,.txt,.md,.markdown,.png,.jpg,.jpeg,.bmp,.tiff,.webp"
          onChange={(e) => mergeFiles(Array.from(e.target.files || []))}
        />
        <div className="neo-box bg-pink-400 p-4 mb-4">
          <Upload className="text-white" size={32} />
        </div>
        <p className="text-xl font-black uppercase tracking-tight">拖拽文件到这里</p>
        <p className="text-xs font-bold opacity-60 mt-2">PDF, Word, TXT, Markdown 或 图片</p>
      </div>

      {/* Selected Files List */}
      {selectedFiles.length > 0 && (
        <div className="neo-box bg-slate-50 p-6">
          <h3 className="text-sm font-black uppercase mb-4 border-b-2 border-slate-900 pb-2 flex justify-between">
            <span>已选文件 ({selectedFiles.length})</span>
            <span className="text-blue-600">{totalMb.toFixed(2)} MB</span>
          </h3>
          <div className="space-y-2 max-h-[200px] overflow-y-auto pr-2 custom-scrollbar">
            {selectedFiles.map((file, idx) => (
              <div key={idx} className="neo-box-sm bg-white p-3 flex items-center justify-between group">
                <div className="flex items-center gap-2 overflow-hidden">
                  <FileText size={16} className="text-slate-400 shrink-0" />
                  <span className="text-xs font-bold truncate">{file.name}</span>
                </div>
                <button 
                  onClick={() => setSelectedFiles(prev => prev.filter((_, i) => i !== idx))}
                  className="text-slate-400 hover:text-pink-500"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-3 border-t-2 border-slate-200 pt-4">
            <span className="text-[11px] font-black uppercase tracking-wider">OCR 模式</span>
            <div className="flex gap-2">
              <button
                type="button"
                className={`neo-button-xs ${ocrEngineChoice === "local" ? "bg-blue-400 text-white" : "bg-white"}`}
                onClick={() => setOcrEngineChoice("local")}
                disabled={uploading}
              >
                本地
              </button>
              <button
                type="button"
                className={`neo-button-xs ${ocrEngineChoice === "baidu" ? "bg-yellow-400" : "bg-white"}`}
                onClick={() => setOcrEngineChoice("baidu")}
                disabled={uploading}
              >
                百度 OCR
              </button>
              <button
                type="button"
                className={`neo-button-xs ${ocrEngineChoice === "glm-ocr" ? "bg-pink-400 text-white" : "bg-white"}`}
                onClick={() => setOcrEngineChoice("glm-ocr")}
                disabled={uploading}
              >
                GLM-OCR
              </button>
            </div>
            <span className="text-[10px] font-bold opacity-60">
              默认使用本地 OCR，不再自动调用付费 OCR API；只有你主动选百度或 GLM 才会走 API。
            </span>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3 border-t-2 border-slate-200 pt-4">
            <span className="text-[11px] font-black uppercase tracking-wider">向量模式</span>
            <div className="flex gap-2">
              <button
                type="button"
                className={`neo-button-xs ${embeddingMode === "local" ? "bg-blue-400 text-white" : "bg-white"}`}
                onClick={() => setEmbeddingMode("local")}
                disabled={uploading}
              >
                本地
              </button>
              <button
                type="button"
                className={`neo-button-xs ${embeddingMode === "api" ? "bg-pink-400 text-white" : "bg-white"}`}
                onClick={() => setEmbeddingMode("api")}
                disabled={uploading}
              >
                API
              </button>
            </div>
            <span className="text-[10px] font-bold opacity-60">
              当前上传与问答检索都会使用这个设置
            </span>
          </div>
          <div className="mt-6 flex gap-3">
            <button
              className="neo-button bg-blue-400 w-full flex items-center justify-center gap-2 disabled:opacity-50"
              disabled={uploading}
              onClick={() => void handleUpload()}
            >
              <CloudUpload size={20} />
              {uploading ? "处理中..." : "开始上传"}
            </button>
            <button 
              className="neo-button bg-white px-4"
              onClick={() => setSelectedFiles([])}
              disabled={uploading}
            >
              清空
            </button>
          </div>
        </div>
      )}

      {/* Progress Section */}
      {(uploading || progress > 0) && (
        <div className="neo-box bg-yellow-400 p-6 rotate-[-0.5deg]">
          <div className="flex justify-between items-end mb-4">
            <div className="flex items-center gap-2">
              <Sparkles size={20} className="animate-pulse" />
              <span className="text-sm font-black uppercase tracking-wider">{phaseText || "Initializing..."}</span>
            </div>
            <span className="text-2xl font-black">{progress}%</span>
          </div>
          
          <div className="neo-box-sm bg-white h-8 overflow-hidden">
            <div 
              className="h-full bg-pink-500 border-r-4 border-slate-900 transition-all duration-500 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>

          <div className="mt-4 grid grid-cols-3 gap-4">
            {[
              { label: '提取', val: extractProgress },
              { label: '向量', val: vectorProgress },
              { label: '索引', val: indexBuildProgress }
            ].map(s => (
              <div key={s.label} className="neo-box-sm bg-white/40 p-2 text-center">
                <p className="text-[10px] font-black uppercase opacity-60">{s.label}</p>
                <p className="text-sm font-black">{s.val}%</p>
              </div>
            ))}
          </div>
          {slowHint && <p className="mt-4 text-xs font-black text-pink-700 animate-bounce text-center italic">正在处理长文档，请稍候！</p>}
        </div>
      )}

      {/* Messages */}
      {localInfo && <div className="neo-box bg-green-400 p-4 text-sm font-black uppercase">{localInfo}</div>}
      {(error || localError) && (
        <div className="neo-box bg-red-400 text-white p-4 text-sm font-black uppercase">
          错误: {error || localError}
        </div>
      )}

      <ModalShell
        open={Boolean(ocrPrompt)}
        onClose={() => finishOcrPrompt(null)}
        panelClassName="relative my-auto w-full max-w-2xl rounded-3xl border-4 border-slate-900 bg-[#fff8dc] p-6 shadow-[10px_10px_0_#0f172a]"
      >
        {ocrPrompt && (
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-black uppercase tracking-[0.3em] text-slate-500">OCR Hint</p>
                <h3 className="mt-2 text-2xl font-black uppercase text-slate-900">
                  {ocrPrompt.kind === "large_scan" ? "Large PDF detected" : "Complex layout suspected"}
                </h3>
              </div>
              <button
                type="button"
                className="neo-button-xs bg-white"
                onClick={() => finishOcrPrompt(null)}
              >
                Cancel
              </button>
            </div>

            <div className="neo-box-sm bg-white p-4 text-sm font-bold text-slate-700">
              {ocrPrompt.kind === "large_scan" ? (
                <p>
                  We heuristically detected a large PDF batch candidate. The largest flagged file is about{" "}
                  {ocrPrompt.maxFileSizeMb.toFixed(1)} MB. The default path will stay local OCR. If you need paid OCR,
                  choose Baidu OCR or GLM-OCR explicitly.
                </p>
              ) : (
                <p>
                  Filename hints suggest that this batch may contain complex layout PDFs. The default path will stay
                  local OCR, but you can switch to GLM-OCR if you need better recovery for tables, forms, exam papers,
                  and scan-like page structures.
                </p>
              )}
            </div>

            <div className="neo-box-sm bg-slate-900 p-4 text-white">
              <p className="text-xs font-black uppercase tracking-wider text-slate-300">Affected Files</p>
              <p className="mt-2 text-sm font-bold break-words">{formatPromptFileNames(ocrPrompt.filenames)}</p>
              <p className="mt-2 text-[11px] font-bold text-slate-300">
                This reminder covers {ocrPrompt.totalPdfCount} PDF file{ocrPrompt.totalPdfCount > 1 ? "s" : ""} in the current batch.
              </p>
            </div>

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                className="neo-button bg-white"
                onClick={() => finishOcrPrompt("local")}
              >
                Continue Local
              </button>
              {ocrPrompt.kind === "large_scan" && (
                <button
                  type="button"
                  className="neo-button bg-blue-400 text-white"
                  onClick={() => finishOcrPrompt("baidu")}
                >
                  Use Baidu OCR
                </button>
              )}
              <button
                type="button"
                className="neo-button bg-pink-400 text-white"
                onClick={() => finishOcrPrompt("glm-ocr")}
              >
                Use GLM-OCR
              </button>
            </div>
          </div>
        )}
      </ModalShell>

      {/* Quota & Info Footer */}
      <div className="neo-box-sm bg-slate-900 text-white p-4 flex flex-wrap justify-between gap-4 text-[10px] font-black uppercase tracking-widest">
        <div className="flex gap-4">
          <span>OCR: {ocrEngineChoice}</span>
          <span>EMBED: {embeddingMode}</span>
          <span>BACKUP: {localStorageName}</span>
        </div>
        {quota && (
          <div className="flex gap-2 items-center">
            <span>存储使用: {Math.round((quota.used_storage_bytes / Math.max(quota.max_storage_bytes, 1)) * 100)}%</span>
            <div className="w-20 h-2 bg-white/20 rounded-full overflow-hidden">
              <div 
                className="h-full bg-green-400" 
                style={{ width: `${Math.round((quota.used_storage_bytes / Math.max(quota.max_storage_bytes, 1)) * 100)}%` }} 
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default UploadTab;
