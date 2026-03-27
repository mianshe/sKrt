import { DragEvent, useEffect, useMemo, useState } from "react";
import { DocumentItem, UploadTaskItem } from "../hooks/useDocuments";
import { GPU_OCR_PAGE_PACKS, GPU_OCR_REDEEM_PAGES } from "../config/gpuOcrPricing";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";
type DeferredInstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
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
  const [gpuQuota, setGpuQuota] = useState<{ used: number; limit: number; paid_balance?: number } | null>(null);
  const [gpuConfirmOpen, setGpuConfirmOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [localError, setLocalError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [redeemOpen, setRedeemOpen] = useState(false);
  const [redeemCode, setRedeemCode] = useState("");
  const [redeemStatus, setRedeemStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [redeemMessage, setRedeemMessage] = useState("");
  const [tapStartMs, setTapStartMs] = useState<number | null>(null);
  const [tapCount, setTapCount] = useState(0);
  const [deferredPrompt, setDeferredPrompt] = useState<DeferredInstallPrompt | null>(null);
  const [installHintOpen, setInstallHintOpen] = useState(false);
  const [installMessage, setInstallMessage] = useState("");
  const [isInstalled, setIsInstalled] = useState(false);
  const [payOpen, setPayOpen] = useState(false);
  const [payStatus, setPayStatus] = useState<"idle" | "creating" | "pending" | "paid" | "error">("idle");
  const [payMessage, setPayMessage] = useState("");
  const [selectedPackKey, setSelectedPackKey] = useState<"A" | "B" | "C">("A");
  const [payChannel, setPayChannel] = useState<"wechat_native" | "alipay_qr">("wechat_native");
  const [orderNo, setOrderNo] = useState("");
  const [orderQrImage, setOrderQrImage] = useState("");

  const pricingText = useMemo(() => {
    return GPU_OCR_PAGE_PACKS.map(
      (p) =>
        `${p.name}：${p.pages}页，¥${p.priceCny}（约 ¥${p.pricePerPageCny.toFixed(4)}/页）`
    ).join("；");
  }, []);
  const selectedPack = useMemo(() => GPU_OCR_PAGE_PACKS.find((x) => x.key === selectedPackKey) ?? GPU_OCR_PAGE_PACKS[0], [selectedPackKey]);

  const quotaHeaders = () => {
    try {
      let cid = localStorage.getItem("xm_client_id") || "";
      if (!cid) {
        cid = crypto.randomUUID();
        localStorage.setItem("xm_client_id", cid);
      }
      const tid = localStorage.getItem("xm_tenant_id") || "public";
      return {
        "X-Client-Id": cid,
        "X-Tenant-Id": tid,
      };
    } catch {
      return {};
    }
  };

  const refreshQuota = async () => {
    try {
      const res = await fetch(`${API_BASE}/gpu/ocr/quota`, { headers: quotaHeaders(), credentials: "include" });
      if (!res.ok) return;
      const data = await res.json();
      const used = typeof data?.used === "number" ? data.used : 0;
      const limit = typeof data?.limit === "number" ? data.limit : 20;
      const paid_balance = typeof data?.paid_balance === "number" ? data.paid_balance : undefined;
      setGpuQuota({ used, limit, paid_balance });
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    void refreshQuota();
  }, []);

  useEffect(() => {
    const standalone = window.matchMedia("(display-mode: standalone)").matches;
    const iosStandalone = (window.navigator as Navigator & { standalone?: boolean }).standalone === true;
    if (standalone || iosStandalone) {
      setIsInstalled(true);
    }

    const onBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      setDeferredPrompt(event as DeferredInstallPrompt);
    };
    const onInstalled = () => {
      setIsInstalled(true);
      setDeferredPrompt(null);
      setInstallMessage("已安装到桌面");
    };
    window.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  const isIosSafari = useMemo(() => {
    const ua = window.navigator.userAgent.toLowerCase();
    const isIos = /iphone|ipad|ipod/.test(ua);
    const isSafari = /safari/.test(ua) && !/crios|fxios|edgios/.test(ua);
    return isIos && isSafari;
  }, []);
  const recommendedPayChannel = useMemo<"wechat_native" | "alipay_qr">(() => {
    const ua = window.navigator.userAgent.toLowerCase();
    if (/alipayclient/.test(ua)) return "alipay_qr";
    return "wechat_native";
  }, []);

  const onInstallClick = async () => {
    setInstallMessage("");
    if (isInstalled) {
      setInstallMessage("应用已安装");
      return;
    }
    if (deferredPrompt) {
      await deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      if (choice.outcome === "accepted") {
        setInstallMessage("安装请求已提交");
      } else {
        setInstallMessage("你已取消安装");
      }
      setDeferredPrompt(null);
      return;
    }
    if (isIosSafari) {
      setInstallHintOpen(true);
      return;
    }
    setInstallMessage("当前环境暂不支持安装，请使用 Chrome/Edge 打开");
  };

  const onQuotaTap = () => {
    const now = Date.now();
    const start = tapStartMs;
    if (!start || now - start > 2000) {
      setTapStartMs(now);
      setTapCount(1);
      return;
    }
    const next = tapCount + 1;
    if (next >= 6) {
      setTapStartMs(null);
      setTapCount(0);
      setRedeemStatus("idle");
      setRedeemMessage("");
      setRedeemCode("");
      setRedeemOpen(true);
      return;
    }
    setTapCount(next);
  };

  const submitRedeem = async () => {
    const code = redeemCode.trim();
    if (!code) {
      setRedeemStatus("error");
      setRedeemMessage("请输入手机随机码");
      return;
    }
    setRedeemStatus("loading");
    setRedeemMessage("");
    try {
      const res = await fetch(`${API_BASE}/gpu/ocr/redeem`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...quotaHeaders() },
        credentials: "include",
        body: JSON.stringify({ code }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "随机码错误或已过期";
        setRedeemStatus("error");
        setRedeemMessage(detail);
        return;
      }
      setRedeemStatus("success");
      setRedeemMessage(`已到账 ${GPU_OCR_REDEEM_PAGES} 页`);
      await refreshQuota();
    } catch {
      setRedeemStatus("error");
      setRedeemMessage("网络错误，请稍后重试");
    }
  };

  const createPayOrder = async () => {
    setPayStatus("creating");
    setPayMessage("");
    setOrderNo("");
    setOrderQrImage("");
    try {
      const res = await fetch(`${API_BASE}/gpu/ocr/pay/order/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...quotaHeaders() },
        credentials: "include",
        body: JSON.stringify({ pack_key: selectedPack.key, channel: payChannel }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "创建订单失败";
        setPayStatus("error");
        setPayMessage(detail);
        return;
      }
      setOrderNo(typeof data?.order_no === "string" ? data.order_no : "");
      setOrderQrImage(typeof data?.qr_image_url === "string" ? data.qr_image_url : "");
      setPayStatus("pending");
      setPayMessage(payChannel === "alipay_qr" ? "请使用支付宝扫码完成支付" : "请使用微信扫码完成支付");
    } catch {
      setPayStatus("error");
      setPayMessage("网络错误，请稍后重试");
    }
  };

  useEffect(() => {
    if (!orderNo || payStatus !== "pending") return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      if (stopped) return;
      try {
        const res = await fetch(`${API_BASE}/gpu/ocr/pay/order/${orderNo}`, {
          headers: quotaHeaders(),
          credentials: "include",
        });
        if (!res.ok) return;
        const data = await res.json();
        const status = typeof data?.status === "string" ? data.status : "";
        if (status === "paid") {
          stopped = true;
          window.clearInterval(timer);
          setPayStatus("paid");
          setPayMessage(`已到账 ${selectedPack.pages} 页`);
          await refreshQuota();
        } else if (status === "refunded" || status === "failed") {
          stopped = true;
          window.clearInterval(timer);
          setPayStatus("error");
          setPayMessage(`订单状态：${status}`);
        }
      } catch {
        // ignore polling errors
      }
    }, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [orderNo, payStatus, selectedPack.pages]);

  const handleUpload = async () => {
    if (!selectedFiles.length) return;
    setLocalError("");
    await refreshQuota();

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
      await refreshQuota();
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
          {gpuQuota && (
            <div className="absolute right-5 top-4 text-right text-[11px] text-slate-500">
              {!isInstalled && (
                <button
                  type="button"
                  className="mb-1 rounded-md bg-white/85 px-2 py-1 text-[11px] text-indigo-600 ring-1 ring-indigo-200 transition hover:bg-indigo-50"
                  onClick={onInstallClick}
                  disabled={uploading}
                  title="安装到桌面/创建快捷方式"
                >
                  安装到桌面
                </button>
              )}
              <button
                type="button"
                className="mb-1 ml-1 rounded-md bg-white/85 px-2 py-1 text-[11px] text-emerald-600 ring-1 ring-emerald-200 transition hover:bg-emerald-50"
                onClick={() => {
                  setPayOpen(true);
                  setPayChannel(recommendedPayChannel);
                  setPayStatus("idle");
                  setPayMessage("");
                  setOrderNo("");
                  setOrderQrImage("");
                }}
                disabled={uploading}
                title="购买页包"
              >
                购买页包
              </button>
              <button
                type="button"
                className="rounded-md px-1 py-0.5 transition hover:bg-slate-100"
                onClick={onQuotaTap}
                disabled={uploading}
                title="连续点击 6 次可兑换页数"
              >
                GPU 本月：{gpuQuota.used}/{gpuQuota.limit}
              </button>
              {typeof gpuQuota.paid_balance === "number" && (
                <div className="mt-0.5 text-[11px] text-slate-400">付费/赠送余额：{gpuQuota.paid_balance}页</div>
              )}
              {installMessage && <div className="mt-0.5 text-[11px] text-slate-400">{installMessage}</div>}
            </div>
          )}
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

        {redeemOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
            <div className="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
              <p className="text-sm font-semibold text-slate-800">兑换加页</p>
              <p className="mt-1 text-xs text-slate-500">请输入手机随机码，成功后将自动刷新额度。</p>
              <input
                className="input mt-3"
                value={redeemCode}
                placeholder="请输入手机随机码"
                onChange={(e) => setRedeemCode(e.target.value)}
                disabled={redeemStatus === "loading"}
              />
              <p className="mt-2 text-[11px] text-slate-400">{pricingText}</p>
              {redeemMessage && (
                <p className={`mt-2 text-xs ${redeemStatus === "success" ? "text-emerald-600" : "text-rose-600"}`}>
                  {redeemMessage}
                </p>
              )}
              <div className="mt-3 flex gap-2">
                <button className="btn-primary" onClick={submitRedeem} disabled={redeemStatus === "loading"}>
                  {redeemStatus === "loading" ? "提交中..." : "提交"}
                </button>
                <button
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={() => setRedeemOpen(false)}
                  disabled={redeemStatus === "loading"}
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        )}

        {payOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
            <div className="w-full max-w-md rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
              <p className="text-sm font-semibold text-slate-800">购买页包（微信/支付宝）</p>
              <p className="mt-1 text-xs text-slate-500">支付完成后会自动到账并刷新余额。</p>
              <div className="mt-3 flex gap-2">
                <button
                  className={`rounded-xl px-3 py-1.5 text-xs ring-1 transition ${
                    payChannel === "wechat_native"
                      ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                      : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
                  }`}
                  onClick={() => setPayChannel("wechat_native")}
                  disabled={payStatus === "creating" || payStatus === "pending"}
                >
                  微信
                </button>
                <button
                  className={`rounded-xl px-3 py-1.5 text-xs ring-1 transition ${
                    payChannel === "alipay_qr"
                      ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                      : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
                  }`}
                  onClick={() => setPayChannel("alipay_qr")}
                  disabled={payStatus === "creating" || payStatus === "pending"}
                >
                  支付宝
                </button>
                <span className="self-center text-[11px] text-slate-400">
                  已自动推荐：{recommendedPayChannel === "alipay_qr" ? "支付宝" : "微信"}
                </span>
              </div>
              <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                {GPU_OCR_PAGE_PACKS.map((pack) => (
                  <button
                    key={pack.key}
                    className={`rounded-xl px-3 py-2 text-left text-xs ring-1 transition ${
                      selectedPackKey === pack.key
                        ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                        : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
                    }`}
                    onClick={() => setSelectedPackKey(pack.key)}
                    disabled={payStatus === "creating" || payStatus === "pending"}
                  >
                    <div className="font-semibold">{pack.name}</div>
                    <div>{pack.pages} 页</div>
                    <div>¥{pack.priceCny}</div>
                  </button>
                ))}
              </div>
              <p className="mt-2 text-[11px] text-slate-400">{pricingText}</p>
              {orderQrImage && (
                <div className="mt-3 flex justify-center">
                  <img src={orderQrImage} alt="微信扫码支付二维码" className="h-48 w-48 rounded-lg ring-1 ring-slate-200" />
                </div>
              )}
              {orderNo && <p className="mt-2 text-[11px] text-slate-400">订单号：{orderNo}</p>}
              {payMessage && (
                <p className={`mt-2 text-xs ${payStatus === "paid" ? "text-emerald-600" : "text-rose-600"}`}>{payMessage}</p>
              )}
              <div className="mt-3 flex gap-2">
                <button
                  className="btn-primary"
                  onClick={createPayOrder}
                  disabled={payStatus === "creating" || payStatus === "pending"}
                >
                  {payStatus === "creating"
                    ? "创建中..."
                    : payStatus === "pending"
                      ? "待支付"
                      : `${payChannel === "alipay_qr" ? "支付宝" : "微信"}支付 ¥${selectedPack.priceCny}`}
                </button>
                <button
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={() => setPayOpen(false)}
                  disabled={payStatus === "creating"}
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        )}

        {installHintOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
            <div className="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
              <p className="text-sm font-semibold text-slate-800">添加到主屏幕</p>
              <p className="mt-1 text-xs text-slate-500">
                iOS Safari 请点击“分享”按钮，然后选择“添加到主屏幕”。
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={() => setInstallHintOpen(false)}
                >
                  我知道了
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
