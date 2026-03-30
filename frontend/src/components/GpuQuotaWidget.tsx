import { useEffect, useMemo, useRef, useState } from "react";
import { GPU_OCR_CALL_PACKS } from "../config/gpuOcrPricing";
import { API_BASE } from "../config/apiBase";
import { useAccessToken } from "../lib/auth";
import { formatApiFetchError } from "../lib/fetchErrors";
import { withTenantHeaders } from "../hooks/useDocuments";
import ModalShell from "./ModalShell";

const GPU_QUOTA_REFRESH_EVENT = "gpu-ocr-quota-refresh";
const PENDING_PAY_ORDER_STORAGE_KEY = "gpu-ocr-pending-pay-order-v1";

type DeferredInstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

type GpuQuota = {
  used: number;
  limit: number;
  paid_balance?: number;
  special?: boolean;
};

type PendingPayOrder = {
  orderNo: string;
  qrImageUrl: string;
  packKey: "A" | "B" | "C";
  channel: "wechat_native" | "alipay_qr";
};

type GpuQuotaWidgetProps = {
  authSession?: number;
};

function readPendingPayOrder(): PendingPayOrder | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PENDING_PAY_ORDER_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PendingPayOrder>;
    if (
      typeof parsed?.orderNo !== "string" ||
      typeof parsed?.qrImageUrl !== "string" ||
      (parsed?.packKey !== "A" && parsed?.packKey !== "B" && parsed?.packKey !== "C") ||
      (parsed?.channel !== "wechat_native" && parsed?.channel !== "alipay_qr")
    ) {
      return null;
    }
    return parsed as PendingPayOrder;
  } catch {
    return null;
  }
}

function writePendingPayOrder(order: PendingPayOrder) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(PENDING_PAY_ORDER_STORAGE_KEY, JSON.stringify(order));
}

function clearPendingPayOrder() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(PENDING_PAY_ORDER_STORAGE_KEY);
}

function broadcastQuotaRefresh() {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(GPU_QUOTA_REFRESH_EVENT));
}

export default function GpuQuotaWidget({ authSession = 0 }: GpuQuotaWidgetProps) {
  const [gpuQuota, setGpuQuota] = useState<GpuQuota>({ used: 0, limit: 20 });
  const [quotaLoadError, setQuotaLoadError] = useState(false);

  const [redeemOpen, setRedeemOpen] = useState(false);
  const redeemOpenRef = useRef(false);
  const redeemReqIdRef = useRef(0);
  const [redeemCode, setRedeemCode] = useState("");
  const [redeemStatus, setRedeemStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [redeemMessage, setRedeemMessage] = useState("");
  const [tapStartMs, setTapStartMs] = useState<number | null>(null);
  const [tapCount, setTapCount] = useState(0);

  const [deferredPrompt, setDeferredPrompt] = useState<DeferredInstallPrompt | null>(null);
  const [installHintOpen, setInstallHintOpen] = useState(false);
  const [installHintText, setInstallHintText] = useState("");
  const [installMessage, setInstallMessage] = useState("");
  const [isInstalled, setIsInstalled] = useState(false);

  const [payOpen, setPayOpen] = useState(false);
  const payOpenRef = useRef(false);
  const payReqIdRef = useRef(0);
  const orderNoRef = useRef("");
  const [payStatus, setPayStatus] = useState<"idle" | "creating" | "pending" | "paid" | "error">("idle");
  const [payMessage, setPayMessage] = useState("");
  const [selectedPackKey, setSelectedPackKey] = useState<"A" | "B" | "C">("A");
  const [payChannel, setPayChannel] = useState<"wechat_native" | "alipay_qr">("wechat_native");
  const [orderNo, setOrderNo] = useState("");
  const [orderQrImage, setOrderQrImage] = useState("");
  const [statusNotice, setStatusNotice] = useState("");

  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);

  const selectedPack = useMemo(
    () => GPU_OCR_CALL_PACKS.find((item) => item.key === selectedPackKey) ?? GPU_OCR_CALL_PACKS[0],
    [selectedPackKey]
  );

  const isIosSafari = useMemo(() => {
    const ua = window.navigator.userAgent.toLowerCase();
    const isIos = /iphone|ipad|ipod/.test(ua);
    const isSafari = /safari/.test(ua) && !/crios|fxios|edgios/.test(ua);
    return isIos && isSafari;
  }, []);

  const installEnv = useMemo(() => {
    const ua = window.navigator.userAgent.toLowerCase();
    const isAndroid = /android/.test(ua);
    const isEdge = /edg\//.test(ua) || /edga\//.test(ua) || /edgios\//.test(ua);
    const isChrome = /chrome\//.test(ua) && !isEdge;
    const isDesktopChromium = !isAndroid && (isChrome || isEdge);
    const isQuark = /quark/.test(ua);
    const isMiuiBrowser = /miuibrowser|mibrowser|xiaomi/.test(ua);
    const isWechat = /micromessenger/.test(ua);
    const isQq = /\sqq\//.test(ua) || /mqqbrowser/.test(ua);
    const isAndroidChromeLike = isAndroid && (isChrome || isEdge) && !isWechat && !isQq && !isQuark && !isMiuiBrowser;
    const isUnsupportedInstallBrowser = isAndroid && (isQuark || isMiuiBrowser || isWechat || isQq);
    return {
      isAndroidChromeLike,
      isDesktopChromium,
      isUnsupportedInstallBrowser,
    };
  }, []);

  const recommendedPayChannel = useMemo<"wechat_native" | "alipay_qr">(() => {
    const ua = window.navigator.userAgent.toLowerCase();
    return /alipayclient/.test(ua) ? "alipay_qr" : "wechat_native";
  }, []);

  const refreshQuota = async () => {
    try {
      const response = await fetch(`${API_BASE}/gpu/ocr/quota`, {
        headers: withTenantHeaders(),
        credentials: "include",
      });
      if (!response.ok) {
        setQuotaLoadError(true);
        return;
      }

      const data = await response.json();
      setQuotaLoadError(false);
      setGpuQuota({
        used: typeof data?.used === "number" ? data.used : 0,
        limit: typeof data?.limit === "number" ? data.limit : 20,
        paid_balance: typeof data?.paid_balance === "number" ? data.paid_balance : undefined,
        special: data?.special === true,
      });
    } catch {
      setQuotaLoadError(true);
    }
  };

  const checkPayOrderStatus = async (currentOrderNo: string, expectedCalls: number) => {
    const response = await fetch(`${API_BASE}/gpu/ocr/pay/order/${currentOrderNo}`, {
      headers: withTenantHeaders(),
      credentials: "include",
    });
    if (!response.ok) return;

    const data = await response.json();
    if (orderNoRef.current !== currentOrderNo) return;

    const status = typeof data?.status === "string" ? data.status : "";
    if (status === "paid") {
      clearPendingPayOrder();
      setPayStatus("paid");
      setPayMessage(`已到账 ${expectedCalls} 次`);
      setStatusNotice(`支付成功，已自动到账 ${expectedCalls} 次`);
      await refreshQuota();
      broadcastQuotaRefresh();
      return;
    }

    if (status === "refunded" || status === "failed") {
      clearPendingPayOrder();
      setPayStatus("error");
      setPayMessage(`订单状态：${status}`);
      setStatusNotice(`订单状态已更新：${status}`);
    }
  };

  const openPayModal = () => {
    setPayOpen(true);
    setStatusNotice("");
    if (orderNo && payStatus === "pending") return;

    const pending = readPendingPayOrder();
    if (pending) {
      setSelectedPackKey(pending.packKey);
      setPayChannel(pending.channel);
      setOrderNo(pending.orderNo);
      setOrderQrImage(pending.qrImageUrl);
      setPayStatus("pending");
      setPayMessage("检测到未完成订单，请继续扫码或等待到账");
      return;
    }

    setPayChannel(recommendedPayChannel);
    setPayStatus("idle");
    setPayMessage("");
    setOrderNo("");
    setOrderQrImage("");
  };

  useEffect(() => {
    if (!loggedIn) {
      setGpuQuota({ used: 0, limit: 0, paid_balance: 0, special: false });
      setQuotaLoadError(false);
      clearPendingPayOrder();
      return;
    }
    void refreshQuota();
  }, [authSession, loggedIn]);

  useEffect(() => {
    redeemOpenRef.current = redeemOpen;
  }, [redeemOpen]);

  useEffect(() => {
    payOpenRef.current = payOpen;
  }, [payOpen]);

  useEffect(() => {
    orderNoRef.current = orderNo;
  }, [orderNo]);

  useEffect(() => {
    const standalone = window.matchMedia("(display-mode: standalone)").matches;
    const iosStandalone = (window.navigator as Navigator & { standalone?: boolean }).standalone === true;
    if (standalone || iosStandalone) setIsInstalled(true);

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

  useEffect(() => {
    if (tapCount <= 0) return;
    const id = window.setTimeout(() => setTapCount(0), 1200);
    return () => window.clearTimeout(id);
  }, [tapCount]);

  useEffect(() => {
    if (!loggedIn) {
      setOrderNo("");
      setOrderQrImage("");
      setPayStatus("idle");
      return;
    }

    const pending = readPendingPayOrder();
    if (!pending) return;

    setSelectedPackKey(pending.packKey);
    setPayChannel(pending.channel);
    setOrderNo(pending.orderNo);
    setOrderQrImage(pending.qrImageUrl);
    setPayStatus("pending");
    setPayMessage("检测到未完成订单，正在自动查询到账状态");
  }, [loggedIn, authSession]);

  useEffect(() => {
    if (!orderNo || payStatus !== "pending") return;

    let stopped = false;
    void checkPayOrderStatus(orderNo, selectedPack.calls);
    const timer = window.setInterval(() => {
      if (stopped) return;
      void checkPayOrderStatus(orderNo, selectedPack.calls);
    }, 2000);

    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [orderNo, payStatus, selectedPack.calls]);

  useEffect(() => {
    if (!loggedIn) return;

    const onQuotaRefresh = () => {
      void refreshQuota();
    };
    const onWindowFocus = () => {
      void refreshQuota();
      const currentOrderNo = orderNoRef.current;
      if (!currentOrderNo) return;
      const pendingPack = GPU_OCR_CALL_PACKS.find((item) => item.key === selectedPackKey) ?? selectedPack;
      void checkPayOrderStatus(currentOrderNo, pendingPack.calls);
    };

    window.addEventListener(GPU_QUOTA_REFRESH_EVENT, onQuotaRefresh);
    window.addEventListener("focus", onWindowFocus);
    document.addEventListener("visibilitychange", onWindowFocus);
    return () => {
      window.removeEventListener(GPU_QUOTA_REFRESH_EVENT, onQuotaRefresh);
      window.removeEventListener("focus", onWindowFocus);
      document.removeEventListener("visibilitychange", onWindowFocus);
    };
  }, [loggedIn, selectedPack, selectedPackKey]);

  useEffect(() => {
    if (payStatus !== "paid") return;
    const timer = window.setTimeout(() => {
      setPayOpen(false);
      setOrderNo("");
      setOrderQrImage("");
      setPayStatus("idle");
      setPayMessage("");
    }, 2200);
    return () => window.clearTimeout(timer);
  }, [payStatus]);

  useEffect(() => {
    if (!statusNotice) return;
    const timer = window.setTimeout(() => setStatusNotice(""), 5000);
    return () => window.clearTimeout(timer);
  }, [statusNotice]);

  const onInstallClick = async () => {
    setInstallMessage("");

    if (isInstalled) {
      setInstallMessage("应用已安装");
      return;
    }

    if (deferredPrompt) {
      await deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      setInstallMessage(choice.outcome === "accepted" ? "已发起安装" : "已取消安装");
      setDeferredPrompt(null);
      return;
    }

    if (isIosSafari) {
      setInstallHintText("iPhone 或 iPad 请点 Safari 底部“分享”，再选择“添加到主屏幕”。");
      setInstallHintOpen(true);
      return;
    }

    if (installEnv.isAndroidChromeLike) {
      setInstallHintText("Android 请点浏览器右上角菜单，选择“安装应用”或“添加到主屏幕”。");
      setInstallHintOpen(true);
      return;
    }

    if (installEnv.isUnsupportedInstallBrowser) {
      setInstallHintText("当前安卓浏览器通常不支持安装，请改用 Chrome 或 Edge 打开后再安装。");
      setInstallHintOpen(true);
      return;
    }

    if (installEnv.isDesktopChromium) {
      setInstallHintText("桌面 Chrome 或 Edge 请点击地址栏右侧安装图标，或从浏览器菜单中选择“安装 sKrt”。");
      setInstallHintOpen(true);
      return;
    }

    setInstallHintText("当前环境暂不支持一键安装，请使用 HTTPS 下的 Chrome、Edge 或 Safari 再试。");
    setInstallHintOpen(true);
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
      setRedeemMessage("正在发送验证码...");
      setRedeemCode("");
      setRedeemOpen(true);
      void sendRedeemCode();
      return;
    }

    setTapCount(next);
  };

  const submitRedeem = async () => {
    const code = redeemCode.trim();
    if (!code) {
      setRedeemStatus("error");
      setRedeemMessage("请输入邮件中的验证码");
      return;
    }

    const reqId = ++redeemReqIdRef.current;
    setRedeemStatus("loading");
    setRedeemMessage("");
    try {
      const response = await fetch(`${API_BASE}/gpu/ocr/redeem`, {
        method: "POST",
        headers: withTenantHeaders({ "Content-Type": "application/json" }),
        credentials: "include",
        body: JSON.stringify({ code }),
      });
      const data = await response.json().catch(() => ({}));
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;

      if (!response.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "验证码错误或已过期";
        setRedeemStatus("error");
        setRedeemMessage(detail);
        return;
      }

      setRedeemStatus("success");
      setRedeemMessage("补充额度已生效");
      await refreshQuota();
      broadcastQuotaRefresh();
    } catch {
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;
      setRedeemStatus("error");
      setRedeemMessage("网络错误，请稍后重试");
    }
  };

  const sendRedeemCode = async () => {
    const reqId = ++redeemReqIdRef.current;
    setRedeemMessage("正在发送验证码...");
    setRedeemStatus("loading");

    try {
      const response = await fetch(`${API_BASE}/gpu/ocr/redeem/send-code`, {
        method: "POST",
        headers: withTenantHeaders(),
        credentials: "include",
      });
      const data = await response.json().catch(() => ({}));
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;

      if (!response.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "发送失败";
        setRedeemStatus("error");
        setRedeemMessage(detail);
        return;
      }

      setRedeemStatus("success");
      setRedeemMessage("验证码已发送到主控邮箱，请查收后填写。");
    } catch (error) {
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;
      setRedeemStatus("error");
      setRedeemMessage(formatApiFetchError(error, "发送失败"));
    }
  };

  const createPayOrder = async () => {
    const reqId = ++payReqIdRef.current;
    setPayStatus("creating");
    setPayMessage("");
    setStatusNotice("");
    setOrderNo("");
    setOrderQrImage("");
    clearPendingPayOrder();

    try {
      const response = await fetch(`${API_BASE}/gpu/ocr/pay/order/create`, {
        method: "POST",
        headers: withTenantHeaders({ "Content-Type": "application/json" }),
        credentials: "include",
        body: JSON.stringify({ pack_key: selectedPack.key, channel: payChannel }),
      });
      const data = await response.json().catch(() => ({}));
      if (!payOpenRef.current || reqId !== payReqIdRef.current) return;

      if (!response.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "创建订单失败";
        setPayStatus("error");
        setPayMessage(detail);
        return;
      }

      const nextOrderNo = typeof data?.order_no === "string" ? data.order_no : "";
      const nextQrImage = typeof data?.qr_image_url === "string" ? data.qr_image_url : "";
      setOrderNo(nextOrderNo);
      setOrderQrImage(nextQrImage);
      setPayStatus("pending");
      setPayMessage(payChannel === "alipay_qr" ? "请使用支付宝扫码完成支付" : "请使用微信扫码完成支付");
      if (nextOrderNo && nextQrImage) {
        writePendingPayOrder({
          orderNo: nextOrderNo,
          qrImageUrl: nextQrImage,
          packKey: selectedPack.key,
          channel: payChannel,
        });
      }
    } catch (error) {
      if (!payOpenRef.current || reqId !== payReqIdRef.current) return;
      setPayStatus("error");
      setPayMessage(formatApiFetchError(error, "网络错误，请稍后重试"));
    }
  };

  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-1.5 text-right text-[11px] text-slate-500">
        {!isInstalled && (
          <button
            type="button"
            className="rounded-md bg-white/85 px-2 py-1 text-[11px] text-indigo-600 ring-1 ring-indigo-200 transition hover:bg-indigo-50"
            onClick={onInstallClick}
          >
            安装到桌面
          </button>
        )}

        <button
          type="button"
          className="rounded-md bg-white/85 px-2 py-1 text-[11px] text-emerald-600 ring-1 ring-emerald-200 transition hover:bg-emerald-50"
          onClick={openPayModal}
        >
          购买次数包
        </button>

        <button
          type="button"
          className="rounded-md bg-white/85 px-2 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200 transition hover:bg-slate-50"
          onClick={onQuotaTap}
        >
          {!loggedIn ? (
            <>外部 OCR 剩余：游客模式</>
          ) : gpuQuota.special ? (
            <>外部 OCR：不限</>
          ) : (
            <>
              外部 OCR 剩余：{typeof gpuQuota.paid_balance === "number" ? gpuQuota.paid_balance : "..."} 次
              {quotaLoadError ? <span className="text-amber-600">（未加载）</span> : null}
            </>
          )}
        </button>

        {installMessage && <span className="ml-1 text-[11px] text-slate-400">{installMessage}</span>}
        {statusNotice && <span className="ml-1 text-[11px] text-emerald-600">{statusNotice}</span>}
      </div>

      <ModalShell
        open={installHintOpen}
        onClose={() => setInstallHintOpen(false)}
        panelClassName="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200"
      >
        <p className="text-sm font-semibold text-slate-800">安装到桌面</p>
        <p className="mt-1 text-xs text-slate-500">{installHintText || "请在浏览器菜单中选择安装或添加到主屏幕。"}</p>
        <div className="mt-3 flex justify-end">
          <button
            className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
            onClick={() => setInstallHintOpen(false)}
          >
            知道了
          </button>
        </div>
      </ModalShell>

      <ModalShell
        open={redeemOpen}
        onClose={() => setRedeemOpen(false)}
        panelClassName="w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200"
      >
        <p className="text-sm font-semibold text-slate-800">主控补额</p>
        <p className="mt-1 text-xs text-slate-500">系统会向配置的主控邮箱发送验证码，验证后可给当前账号补充外部 OCR 额度。</p>
        <input
          className="input mt-3 w-full"
          value={redeemCode}
          placeholder="请输入邮件中的验证码"
          onChange={(e) => setRedeemCode(e.target.value)}
          autoComplete="one-time-code"
        />
        {redeemMessage && (
          <p
            className={`mt-2 text-xs ${
              redeemStatus === "success" ? "text-emerald-600" : redeemStatus === "error" ? "text-rose-600" : "text-slate-600"
            }`}
          >
            {redeemMessage}
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          <button className="btn-primary" onClick={submitRedeem} disabled={!redeemCode.trim() || redeemStatus === "loading"}>
            {redeemStatus === "loading" && redeemCode.trim() ? "提交中..." : "确认"}
          </button>
          <button
            className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
            onClick={() => {
              setRedeemOpen(false);
              setRedeemCode("");
              setRedeemMessage("");
              setRedeemStatus("idle");
            }}
          >
            关闭
          </button>
        </div>
      </ModalShell>

      <ModalShell
        open={payOpen}
        onClose={() => setPayOpen(false)}
        panelClassName="w-full max-w-md rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200"
      >
        <p className="text-sm font-semibold text-slate-800">购买外部 OCR 次数包</p>
        <p className="mt-1 text-xs text-slate-500">支付完成后会自动到账。即使关闭弹窗，系统也会继续轮询订单状态。</p>

        <ul className="mt-2 space-y-1 rounded-xl bg-slate-50/80 px-3 py-2 text-[11px] text-slate-600 ring-1 ring-slate-100">
          {GPU_OCR_CALL_PACKS.map((pack) => (
            <li key={pack.key}>
              <span className="font-medium text-slate-700">{pack.name}</span>
              ：共 <strong>{pack.calls}</strong> 次，总价 <strong>¥{pack.priceCny}</strong>，单次约{" "}
              <strong>¥{pack.pricePerCallCny.toFixed(4)}</strong>
            </li>
          ))}
        </ul>

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
            默认推荐：{recommendedPayChannel === "alipay_qr" ? "支付宝" : "微信"}
          </span>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          {GPU_OCR_CALL_PACKS.map((pack) => (
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
              <div className="mt-0.5 text-[11px] text-slate-500">
                {pack.calls} 次 · ¥{pack.priceCny}
              </div>
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <button className="btn-primary" onClick={createPayOrder} disabled={payStatus === "creating" || payStatus === "pending"}>
            {payStatus === "creating" ? "创建中..." : payStatus === "pending" ? "等待支付..." : "生成二维码"}
          </button>
          {orderNo && payStatus === "pending" && (
            <button
              className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
              onClick={() => {
                void checkPayOrderStatus(orderNo, selectedPack.calls);
              }}
            >
              刷新状态
            </button>
          )}
          <button
            className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
            onClick={() => setPayOpen(false)}
          >
            关闭
          </button>
        </div>

        {payMessage && <p className="mt-2 text-xs text-slate-600">{payMessage}</p>}
        {orderNo && <p className="mt-1 text-[11px] text-slate-400">订单号：{orderNo}</p>}
        {orderQrImage && (
          <div className="mt-3 flex items-center justify-center">
            <img src={orderQrImage} alt="支付二维码" className="h-56 w-56 rounded-xl ring-1 ring-slate-200" />
          </div>
        )}
      </ModalShell>
    </>
  );
}
