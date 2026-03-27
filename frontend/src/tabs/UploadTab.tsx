import { DragEvent, useMemo, useState } from "react";
import { DocumentItem, UploadTaskItem } from "../hooks/useDocuments";
import { GPU_OCR_PAGE_PACKS } from "../config/gpuOcrPricing";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

type Props = {
  documents: DocumentItem[];
  loading: boolean;
  error: string;
  onCreateUploadTasks: (
    files: File[],
    discipline: string,
    documentType: string,
    onUploadProgress?: (percent: number) => void,
    options?: { use_gpu_ocr?: boolean }
  ) => Promise<UploadTaskItem[]>;
  onGetTask: (taskId: number) => Promise<UploadTaskItem>;
  onDelete: (id: number) => Promise<void>;
  onRefresh: () => Promise<void>;
};

function UploadTab({ documents, loading, error, onCreateUploadTasks, onGetTask, onDelete, onRefresh }: Props) {
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [parseProgress, setParseProgress] = useState(0);
  const [phaseText, setPhaseText] = useState("");
  const [slowHint, setSlowHint] = useState(false);
  const [useGpuOcr, setUseGpuOcr] = useState(false);
  const [gpuConfirmOpen, setGpuConfirmOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [localError, setLocalError] = useState("");
  const [dragging, setDragging] = useState(false);

  const pricingText = useMemo(() => {
    return GPU_OCR_PAGE_PACKS.map(
      (p) =>
        `${p.name}：${p.pages}页，¥${p.priceCny}（约 ¥${p.pricePerPageCny.toFixed(4)}/页）`
    ).join("；");
  }, []);
  const handleUpload = async () => {
    if (!selectedFiles.length) return;
    setLocalError("");

    const willTryGpu = useGpuOcr || selectedFiles.some((f) => f.size > 15 * 1024 * 1024);
    if (willTryGpu && !gpuConfirmOpen) {
      setGpuConfirmOpen(true);
      return;
    }

    setUploading(true);
    setUploadProgress(0);
    setParseProgress(0);
    setPhaseText("正在上传文件...");
    setSlowHint(false);
    try {
      // 学科与文档类型由后端在解析阶段自主判断，前端不再暴露选择器。
      const tasks = await onCreateUploadTasks(selectedFiles, "all", "academic", setUploadProgress, { use_gpu_ocr: useGpuOcr });
      if (!tasks.length) {
        throw new Error("未创建上传任务");
      }
      setUploadProgress(100);
      setPhaseText("上传完成，正在解析文档...");
      const taskIds = tasks.map((t) => t.task_id);
      let done = false;
      while (!done) {
        await new Promise((resolve) => setTimeout(resolve, 900));
        const latest = await Promise.all(taskIds.map((taskId) => onGetTask(taskId)));
        const avg = latest.reduce((sum, item) => sum + (item.progress_percent || 0), 0) / latest.length;
        setParseProgress(Math.max(0, Math.min(100, Math.round(avg))));
        setSlowHint(latest.some((t) => (t.page_count || 0) > 100 && t.status !== "completed"));

        const phase = latest.find((item) => item.status !== "completed")?.phase || "completed";
        if (phase === "parsing") {
          setPhaseText("正在解析文档...");
        } else if (phase === "indexing") {
          setPhaseText("正在建立索引...");
        } else if (phase === "completed") {
          setPhaseText("上传与解析已完成");
        } else if (phase === "failed") {
          setPhaseText("任务失败");
        } else {
          setPhaseText("任务排队中...");
        }

        const hasFailed = latest.some((item) => item.status === "failed");
        const allDone = latest.every((item) => item.status === "completed");
        if (hasFailed) {
          const failed = latest.find((item) => item.status === "failed");
          throw new Error(failed?.error_message || "上传任务失败");
        }
        done = allDone;
      }
      await onRefresh();
      setSelectedFiles([]);
    } catch (e) {
      setLocalError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setUploading(false);
      setGpuConfirmOpen(false);
    }
  };

  const mergeFiles = (incoming: File[]) => {
    const map = new Map<string, File>();
    [...selectedFiles, ...incoming].forEach((f) => map.set(`${f.name}-${f.size}-${f.lastModified}`, f));
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

  return (
    <section className="space-y-3">
      <div className="card p-4">
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
          <p className="mt-3 text-sm font-semibold text-violet-600">拖拽到这里，开始你的专属资料解析</p>
          <p className="mt-1 text-xs text-slate-400">支持 PDF · DOCX · PPTX · TXT · MD · PNG · JPG 等格式</p>
          {selectedFiles.length > 0 && (
            <p className="mt-1 text-xs text-slate-500">
              已选择 {selectedFiles.length} 个文件（总大小{" "}
              {(selectedFiles.reduce((sum, f) => sum + f.size, 0) / (1024 * 1024)).toFixed(2)} MB）
            </p>
          )}
          <label className="mt-2 inline-flex select-none items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              className="h-4 w-4 accent-violet-600"
              checked={useGpuOcr}
              disabled={uploading}
              onChange={(e) => setUseGpuOcr(e.target.checked)}
            />
            本次用 GPU OCR（计入次数）
          </label>
          {slowHint && (
            <p className="mt-1 text-xs text-slate-500">长文本解析会有点久哦，可以先去做别的事</p>
          )}
          {(useGpuOcr || selectedFiles.some((f) => f.size > 15 * 1024 * 1024)) && (
            <p className="mt-1 text-xs text-slate-500">
              此为扫描书籍式pdf，需要走gpu通道，测试版非特殊用户每天限用一次
            </p>
          )}
        </div>

        {gpuConfirmOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
            <div className="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
              <p className="text-sm font-semibold text-slate-800">文件解析复杂需要gpu，是否继续</p>
              <p className="mt-1 text-xs text-slate-500">继续将消耗本月全站 GPU 额度（特殊用户不受限）。</p>
              <div className="mt-3 flex gap-2">
                <button className="btn-primary" onClick={() => handleUpload()} disabled={uploading}>
                  继续
                </button>
                <button
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={() => setGpuConfirmOpen(false)}
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

        <div className="mt-3 flex gap-2">
          <button className="btn-primary" disabled={loading || uploading || !selectedFiles.length} onClick={handleUpload}>
            {uploading ? "处理中..." : "开始上传"}
          </button>
          <button className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-violet-600 ring-1 ring-violet-200 transition hover:bg-violet-50" onClick={onRefresh}>
            刷新
          </button>
        </div>
        {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
        {localError && <p className="mt-2 text-xs text-rose-600">{localError}</p>}
      </div>

      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-violet-600">✦ 文档管理</h3>
        <div className="space-y-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center justify-between rounded-2xl bg-gradient-to-r from-white to-violet-50/70 px-3 py-2 ring-1 ring-violet-100"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{doc.filename || doc.title}</p>
                <p className="text-xs text-slate-500">
                  {doc.discipline} · {doc.document_type}
                </p>
              </div>
              <button className="btn-danger" onClick={() => onDelete(doc.id)}>
                删除
              </button>
            </div>
          ))}
          {documents.length === 0 && <p className="text-xs text-slate-500">暂无文档</p>}
        </div>
      </div>
    </section>
  );
}

export default UploadTab;
