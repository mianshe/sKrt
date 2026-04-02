import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE } from "../config/apiBase";
import { PAY_PRODUCTS, describePayProduct, formatPayChannel, getPayProduct, listPayProducts, type PayChannel, type PayProduct, type PayProductType } from "../config/payProducts";
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

type ComplexOcrQuota = {
  paid_tokens?: number;
  special?: boolean;
};

type PendingPayOrder = {
  orderNo: string;
  qrImageUrl: string;
  payPageUrl?: string;
  provider: string;
  productType: PayProductType;
  productKey: string;
  channel: PayChannel;
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
      (parsed?.payPageUrl != null && typeof parsed.payPageUrl !== "string") ||
      typeof parsed?.provider !== "string" ||
      (parsed?.productType !== "ocr_calls" && parsed?.productType !== "ocr_tokens" && parsed?.productType !== "cloud_capacity") ||
      typeof parsed?.productKey !== "string" ||
      !parsed.productKey ||
      (parsed?.channel !== "wechat_native" && parsed?.channel !== "alipay_qr" && parsed?.channel !== "paypal")
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

function productCreditText(product: PayProduct): string {
  if (product.type === "ocr_calls") return `${product.calls ?? 0} 次`;
  if (product.type === "ocr_tokens") return `${product.tokens ?? 0} token`;
  const storageGb = Math.round((product.storageBytes ?? 0) / (1024 * 1024 * 1024));
  return `+${product.docBonus ?? 0} 文档 / ${storageGb}GB`;
}

export default function GpuQuotaWidget({ authSession = 0 }: GpuQuotaWidgetProps) {
  const [gpuQuota, setGpuQuota] = useState<GpuQuota>({ used: 0, limit: 20 });
  const [complexOcrQuota, setComplexOcrQuota] = useState<ComplexOcrQuota>({ paid_tokens: 0, special: false });
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
  const [payProvider, setPayProvider] = useState("easypay");
  const [availableProviders, setAvailableProviders] = useState<string[]>(["easypay"]);
  const [providerChannels, setProviderChannels] = useState<Record<string, PayChannel[]>>({ easypay: ["wechat_native", "alipay_qr"] });
  const [supportedChannels, setSupportedChannels] = useState<PayChannel[]>(["wechat_native", "alipay_qr"]);
  const [selectedProductType, setSelectedProductType] = useState<PayProductType>("ocr_calls");
  const [selectedProductKey, setSelectedProductKey] = useState("A");
  const [payChannel, setPayChannel] = useState<PayChannel>("wechat_native");
  const [orderNo, setOrderNo] = useState("");
  const [orderQrImage, setOrderQrImage] = useState("");
  const [orderPayPageUrl, setOrderPayPageUrl] = useState("");
  const [statusNotice, setStatusNotice] = useState("");

  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);

  const selectedProduct = useMemo<PayProduct>(
    () => getPayProduct(selectedProductType, selectedProductKey) ?? PAY_PRODUCTS[0],
    [selectedProductKey, selectedProductType]
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

  const recommendedPayChannel = useMemo<PayChannel>(() => {
    if (payProvider === "paypal") return "paypal";
    const ua = window.navigator.userAgent.toLowerCase();
    return /alipayclient/.test(ua) ? "alipay_qr" : "wechat_native";
  }, [payProvider]);

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

  const refreshComplexOcrQuota = async () => {
    try {
      const response = await fetch(`${API_BASE}/ocr/token/quota`, {
        headers: withTenantHeaders(),
        credentials: "include",
      });
      if (!response.ok) return;
      const data = await response.json();
      setComplexOcrQuota({
        paid_tokens: typeof data?.paid_tokens === "number" ? data.paid_tokens : 0,
        special: data?.special === true,
      });
    } catch {
      // ignore
    }
  };

  const refreshPayConfig = async () => {
    try {
      const response = await fetch(`${API_BASE}/gpu/ocr/pay/config`, {
        headers: withTenantHeaders(),
        credentials: "include",
      });
      if (!response.ok) return;
      const data = await response.json();
      const providers = Array.isArray(data?.providers)
        ? data.providers.filter((item: unknown): item is string => typeof item === "string" && item.length > 0)
        : [];
      const provider = typeof data?.provider === "string" ? data.provider : providers[0] || "easypay";
      const channelMap: Record<string, PayChannel[]> =
        data?.provider_channels && typeof data.provider_channels === "object" ? data.provider_channels : {};
      const channels = Array.isArray(data?.supported_channels)
        ? (data.supported_channels.filter(
            (item: unknown): item is PayChannel =>
              item === "wechat_native" || item === "alipay_qr" || item === "paypal"
          ) as PayChannel[])
        : [];
      setAvailableProviders(providers.length > 0 ? providers : [provider]);
      setProviderChannels(channelMap);
      setPayProvider(provider);
      if (channels.length > 0) {
        setSupportedChannels(channels);
        setPayChannel((current) => (channels.includes(current) ? current : channels[0]));
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    const channels = providerChannels[payProvider];
    if (Array.isArray(channels) && channels.length > 0) {
      setSupportedChannels(channels);
      setPayChannel((current) => (channels.includes(current) ? current : channels[0]));
    }
  }, [payProvider, providerChannels]);

  const checkPayOrderStatus = async (currentOrderNo: string, product: PayProduct) => {
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
      const creditText = productCreditText(product);
      setPayMessage(`已到账 ${creditText}`);
      setStatusNotice(`支付成功，已自动到账 ${creditText}`);
      await refreshQuota();
      await refreshComplexOcrQuota();
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
      setPayProvider(pending.provider || payProvider);
      setSelectedProductType(pending.productType);
      setSelectedProductKey(pending.productKey);
      setPayChannel(pending.channel);
      setOrderNo(pending.orderNo);
      setOrderQrImage(pending.qrImageUrl);
      setOrderPayPageUrl(typeof pending.payPageUrl === "string" ? pending.payPageUrl : "");
      setPayStatus("pending");
      setPayMessage("检测到未完成订单，请继续扫码或等待到账");
      return;
    }

    setPayChannel(supportedChannels.includes(recommendedPayChannel) ? recommendedPayChannel : supportedChannels[0] ?? "wechat_native");
    setPayStatus("idle");
    setPayMessage("");
    setOrderNo("");
    setOrderQrImage("");
    setOrderPayPageUrl("");
  };

  useEffect(() => {
    if (!loggedIn) {
      setGpuQuota({ used: 0, limit: 0, paid_balance: 0, special: false });
      setComplexOcrQuota({ paid_tokens: 0, special: false });
      setQuotaLoadError(false);
      clearPendingPayOrder();
      return;
    }
    void refreshQuota();
    void refreshComplexOcrQuota();
    void refreshPayConfig();
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
      setOrderPayPageUrl("");
      setPayStatus("idle");
      return;
    }

    const pending = readPendingPayOrder();
    if (!pending) return;

    setSelectedProductType(pending.productType);
    setSelectedProductKey(pending.productKey);
    setPayProvider(pending.provider || payProvider);
    setPayChannel(pending.channel);
    setOrderNo(pending.orderNo);
    setOrderQrImage(pending.qrImageUrl);
    setOrderPayPageUrl(typeof pending.payPageUrl === "string" ? pending.payPageUrl : "");
    setPayStatus("pending");
    setPayMessage("检测到未完成订单，正在自动查询到账状态");
  }, [loggedIn, authSession]);

  useEffect(() => {
    if (!orderNo || payStatus !== "pending") return;

    let stopped = false;
    void checkPayOrderStatus(orderNo, selectedProduct);
    const timer = window.setInterval(() => {
      if (stopped) return;
      void checkPayOrderStatus(orderNo, selectedProduct);
    }, 2000);

    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [orderNo, payStatus, selectedProduct]);

  useEffect(() => {
    if (!loggedIn) return;

    const onQuotaRefresh = () => {
      void refreshQuota();
    };
    const onWindowFocus = () => {
      void refreshQuota();
      const currentOrderNo = orderNoRef.current;
      if (!currentOrderNo) return;
      const pendingProduct = getPayProduct(selectedProductType, selectedProductKey) ?? selectedProduct;
      void checkPayOrderStatus(currentOrderNo, pendingProduct);
    };

    window.addEventListener(GPU_QUOTA_REFRESH_EVENT, onQuotaRefresh);
    window.addEventListener("focus", onWindowFocus);
    document.addEventListener("visibilitychange", onWindowFocus);
    return () => {
      window.removeEventListener(GPU_QUOTA_REFRESH_EVENT, onQuotaRefresh);
      window.removeEventListener("focus", onWindowFocus);
      document.removeEventListener("visibilitychange", onWindowFocus);
    };
  }, [loggedIn, selectedProduct, selectedProductKey, selectedProductType]);

  useEffect(() => {
    if (payStatus !== "paid") return;
    const timer = window.setTimeout(() => {
      setPayOpen(false);
      setOrderNo("");
      setOrderQrImage("");
      setOrderPayPageUrl("");
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
    setOrderPayPageUrl("");
    clearPendingPayOrder();

    try {
      const response = await fetch(`${API_BASE}/gpu/ocr/pay/order/create`, {
        method: "POST",
        headers: withTenantHeaders({ "Content-Type": "application/json" }),
        credentials: "include",
        body: JSON.stringify({
          provider: payProvider,
          product_type: selectedProduct.type,
          product_key: selectedProduct.key,
          channel: payChannel,
        }),
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
      const nextPayPageUrl = typeof data?.pay_page_url === "string" ? data.pay_page_url : "";
      const payHint = typeof data?.pay_hint === "string" ? data.pay_hint : "";
      setOrderNo(nextOrderNo);
      setOrderQrImage(nextQrImage);
      setOrderPayPageUrl(nextPayPageUrl);
      setPayStatus("pending");
      setPayMessage(payHint || (payChannel === "paypal" ? "请在新页面完成 PayPal 支付" : `请使用${formatPayChannel(payChannel)}完成支付`));
      if (nextPayPageUrl) {
        setPayMessage("支付页已生成，可直接打开支付页继续完成付款");
      }
      if (nextOrderNo && (nextQrImage || nextPayPageUrl)) {
        writePendingPayOrder({
          orderNo: nextOrderNo,
          qrImageUrl: nextQrImage,
          payPageUrl: nextPayPageUrl,
          provider: payProvider,
          productType: selectedProduct.type,
          productKey: selectedProduct.key,
          channel: payChannel,
        });
      }
    } catch (error) {
      if (!payOpenRef.current || reqId !== payReqIdRef.current) return;
      setPayStatus("error");
      setPayMessage(formatApiFetchError(error, "网络错误，请稍后重试"));
    }
  };

  const recreatePayOrder = () => {
    clearPendingPayOrder();
    setOrderNo("");
    setOrderQrImage("");
    setOrderPayPageUrl("");
    setPayStatus("idle");
    setPayMessage("已清除旧订单，正在重新创建");
    setStatusNotice("");
    void createPayOrder();
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

        {loggedIn && (
          <span className="rounded-md bg-white/85 px-2 py-1 text-[11px] text-amber-700 ring-1 ring-amber-200">
            {complexOcrQuota.special
              ? "复杂 OCR：不限"
              : `复杂 OCR token：${typeof complexOcrQuota.paid_tokens === "number" ? complexOcrQuota.paid_tokens : "..."}`}
          </span>
        )}

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

        <div className="mt-3 flex flex-wrap gap-2">
          {(["ocr_calls", "ocr_tokens", "cloud_capacity"] as PayProductType[]).map((type) => (
            <button
              key={type}
              className={`rounded-xl px-3 py-1.5 text-xs ring-1 transition ${
                selectedProductType === type
                  ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                  : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
              }`}
              onClick={() => {
                setSelectedProductType(type);
                setSelectedProductKey(listPayProducts(type)[0]?.key ?? "");
              }}
              disabled={payStatus === "creating" || payStatus === "pending"}
            >
              {type === "ocr_calls" ? "OCR 次数包" : type === "ocr_tokens" ? "复杂 OCR Token" : "云端容量包"}
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {availableProviders.map((provider) => (
            <button
              key={provider}
              className={`rounded-xl px-3 py-1.5 text-xs ring-1 transition ${
                payProvider === provider
                  ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                  : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
              }`}
              onClick={() => setPayProvider(provider)}
              disabled={payStatus === "creating" || payStatus === "pending"}
            >
              {provider === "paypal" ? "PayPal 直连" : provider === "xpay" ? "XPay 聚合" : provider}
            </button>
          ))}
        </div>

        <div className="mt-3 flex gap-2">
          <button
            className={`rounded-xl px-3 py-1.5 text-xs ring-1 transition ${
              payChannel === "wechat_native"
                ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
            }`}
            onClick={() => setPayChannel("wechat_native")}
            disabled={payStatus === "creating" || payStatus === "pending" || !supportedChannels.includes("wechat_native")}
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
            disabled={payStatus === "creating" || payStatus === "pending" || !supportedChannels.includes("alipay_qr")}
          >
            支付宝
          </button>
          <button
            className={`rounded-xl px-3 py-1.5 text-xs ring-1 transition ${
              payChannel === "paypal"
                ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
            }`}
            onClick={() => setPayChannel("paypal")}
            disabled={payStatus === "creating" || payStatus === "pending" || !supportedChannels.includes("paypal")}
          >
            PayPal
          </button>
          <span className="self-center text-[11px] text-slate-400">
            默认推荐：{recommendedPayChannel === "alipay_qr" ? "支付宝" : "微信"}
          </span>
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
          {listPayProducts(selectedProductType).map((product) => (
            <button
              key={product.key}
              className={`rounded-xl px-3 py-2 text-left text-xs ring-1 transition ${
                selectedProductKey === product.key
                  ? "bg-emerald-50 text-emerald-700 ring-emerald-300"
                  : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"
              }`}
              onClick={() => setSelectedProductKey(product.key)}
              disabled={payStatus === "creating" || payStatus === "pending"}
            >
              <div className="font-semibold">{product.name}</div>
              <div className="mt-0.5 text-[11px] text-slate-500">{describePayProduct(product)}</div>
            </button>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <button className="btn-primary" onClick={createPayOrder} disabled={payStatus === "creating" || payStatus === "pending"}>
            {payStatus === "creating" ? "创建中..." : payStatus === "pending" ? "等待支付..." : "生成二维码"}
          </button>
          {orderPayPageUrl && payStatus === "pending" && (
            <button
              className="rounded-2xl bg-emerald-50 px-3 py-2 text-sm text-emerald-700 ring-1 ring-emerald-200 transition hover:bg-emerald-100"
              onClick={() => window.open(orderPayPageUrl, "_blank", "noopener,noreferrer")}
            >
              打开支付页
            </button>
          )}
          {orderNo && payStatus === "pending" && (
            <button
              className="rounded-2xl bg-amber-50 px-3 py-2 text-sm text-amber-700 ring-1 ring-amber-200 transition hover:bg-amber-100"
              onClick={recreatePayOrder}
            >
              重新下单
            </button>
          )}
          {orderNo && payStatus === "pending" && (
            <button
              className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
              onClick={() => {
                void checkPayOrderStatus(orderNo, selectedProduct);
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
