import { useEffect, useMemo, useRef, useState } from "react";
import { GPU_OCR_CALL_PACKS } from "../config/gpuOcrPricing";
import { formatApiFetchError } from "../lib/fetchErrors";
import { getAccessToken } from "../hooks/useDocuments";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

type DeferredInstallPrompt = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;
};

type GpuQuota = { used: number; limit: number; paid_balance?: number; special?: boolean };

const modalBackdropClass = "fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4";
const modalPanelClass = "w-full max-w-sm rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200";

function quotaHeaders() {
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
}

type GpuQuotaWidgetProps = { authSession?: number };

export default function GpuQuotaWidget({ authSession = 0 }: GpuQuotaWidgetProps) {
  /** 额度接口失败时仍展示安装/购买/兑换，避免整栏空白 */
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
  const [payStatus, setPayStatus] = useState<"idle" | "creating" | "pending" | "paid" | "error">("idle");
  const [payMessage, setPayMessage] = useState("");
  const [selectedPackKey, setSelectedPackKey] = useState<"A" | "B" | "C">("A");
  const [payChannel, setPayChannel] = useState<"wechat_native" | "alipay_qr">("wechat_native");
  const [orderNo, setOrderNo] = useState("");
  const [orderQrImage, setOrderQrImage] = useState("");

  const selectedPack = useMemo(
    () => GPU_OCR_CALL_PACKS.find((x) => x.key === selectedPackKey) ?? GPU_OCR_CALL_PACKS[0],
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
    const isQuark = /quark/.test(ua);
    const isMiuiBrowser = /miuibrowser|mibrowser|xiaomi/.test(ua);
    const isWechat = /micromessenger/.test(ua);
    const isQq = /\sqq\//.test(ua) || /mqqbrowser/.test(ua);
    const isAndroidChromeLike = isAndroid && (isChrome || isEdge) && !isWechat && !isQq && !isQuark && !isMiuiBrowser;
    const isUnsupportedInstallBrowser = isAndroid && (isQuark || isMiuiBrowser || isWechat || isQq);
    return { isAndroid, isEdge, isChrome, isQuark, isMiuiBrowser, isWechat, isQq, isAndroidChromeLike, isUnsupportedInstallBrowser };
  }, []);
  const recommendedPayChannel = useMemo<"wechat_native" | "alipay_qr">(() => {
    const ua = window.navigator.userAgent.toLowerCase();
    if (/alipayclient/.test(ua)) return "alipay_qr";
    return "wechat_native";
  }, []);

  const refreshQuota = async () => {
    try {
      const res = await fetch(`${API_BASE}/gpu/ocr/quota`, { headers: quotaHeaders(), credentials: "include" });
      if (!res.ok) {
        setQuotaLoadError(true);
        return;
      }
      setQuotaLoadError(false);
      const data = await res.json();
      const used = typeof data?.used === "number" ? data.used : 0;
      const limit = typeof data?.limit === "number" ? data.limit : 20;
      const paid_balance = typeof data?.paid_balance === "number" ? data.paid_balance : undefined;
      const special = data?.special === true;
      setGpuQuota({ used, limit, paid_balance, special });
    } catch {
      setQuotaLoadError(true);
    }
  };

  useEffect(() => {
    if (!getAccessToken()) {
      setGpuQuota({ used: 0, limit: 0, paid_balance: 0, special: false });
      setQuotaLoadError(false);
      return;
    }
    void refreshQuota();
  }, [authSession]);

  useEffect(() => {
    redeemOpenRef.current = redeemOpen;
  }, [redeemOpen]);

  useEffect(() => {
    payOpenRef.current = payOpen;
  }, [payOpen]);

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

  const onInstallClick = async () => {
    setInstallMessage("");
    if (isInstalled) {
      setInstallMessage("应用已安装");
      return;
    }
    if (deferredPrompt) {
      await deferredPrompt.prompt();
      const choice = await deferredPrompt.userChoice;
      setInstallMessage(choice.outcome === "accepted" ? "安装请求已提交" : "你已取消安装");
      setDeferredPrompt(null);
      return;
    }
    if (isIosSafari) {
      setInstallHintText("iOS Safari：点击底部“分享”按钮，然后选择“添加到主屏幕”。");
      setInstallHintOpen(true);
      return;
    }
    if (installEnv.isAndroidChromeLike) {
      setInstallHintText("Android：点击右上角菜单（⋮/…）→「安装应用」或「添加到主屏幕」。若仍无该选项，请确认已使用 HTTPS 且允许通知/存储权限。");
      setInstallHintOpen(true);
      return;
    }
    if (installEnv.isUnsupportedInstallBrowser) {
      setInstallHintText("当前浏览器通常不支持安装到桌面。请复制链接到 Chrome/Edge 打开后再安装。");
      setInstallHintOpen(true);
      return;
    }
    setInstallHintText("当前环境暂不支持安装，请使用 Chrome/Edge（HTTPS）打开。");
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
      setRedeemMessage("正在发送随机码…");
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
      setRedeemMessage("请填写邮件中的验证码");
      return;
    }
    const reqId = ++redeemReqIdRef.current;
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
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;
      if (!res.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "随机码错误或已过期";
        setRedeemStatus("error");
        setRedeemMessage(detail);
        return;
      }
      setRedeemStatus("success");
      setRedeemMessage("主控补额已生效");
      await refreshQuota();
    } catch {
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;
      setRedeemStatus("error");
      setRedeemMessage("网络错误，请稍后重试");
    }
  };

  const sendRedeemCode = async () => {
    const reqId = ++redeemReqIdRef.current;
    setRedeemMessage("正在发送随机码…");
    setRedeemStatus("loading");
    try {
      const res = await fetch(`${API_BASE}/gpu/ocr/redeem/send-code`, {
        method: "POST",
        headers: { ...quotaHeaders() },
        credentials: "include",
      });
      const data = await res.json().catch(() => ({}));
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;
      if (!res.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : "发送失败";
        setRedeemStatus("error");
        setRedeemMessage(detail);
        return;
      }
      setRedeemStatus("success");
      setRedeemMessage("随机码已发送至主控邮箱（CODE_EMAIL_TO），请查收并填写下方");
    } catch (e) {
      if (!redeemOpenRef.current || reqId !== redeemReqIdRef.current) return;
      setRedeemStatus("error");
      setRedeemMessage(formatApiFetchError(e, "发送失败"));
    }
  };

  const createPayOrder = async () => {
    const reqId = ++payReqIdRef.current;
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
      if (!payOpenRef.current || reqId != payReqIdRef.current) return;
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
    } catch (e) {
      if (!payOpenRef.current || reqId != payReqIdRef.current) return;
      setPayStatus("error");
      setPayMessage(formatApiFetchError(e, "网络错误，请稍后重试"));
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
        if (!payOpenRef.current) return;
        const status = typeof data?.status === "string" ? data.status : "";
        if (status === "paid") {
          stopped = true;
          window.clearInterval(timer);
          setPayStatus("paid");
          setPayMessage(`已到账 ${selectedPack.calls} 次`);
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
  }, [orderNo, payStatus, selectedPack.calls]);

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
          onClick={() => {
            setPayOpen(true);
            setPayChannel(recommendedPayChannel);
            setPayStatus("idle");
            setPayMessage("");
            setOrderNo("");
            setOrderQrImage("");
          }}
        >
          购买次数包
        </button>
        <button
          type="button"
          className="rounded-md bg-white/85 px-2 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200 transition hover:bg-slate-50"
          onClick={onQuotaTap}
        >
          {(() => {
            const loggedIn = Boolean(getAccessToken());
            if (!loggedIn) return <>外部 OCR 剩余：0 次（访客）</>;
            if (gpuQuota.special) return <>外部 OCR：不限</>;
            const n = typeof gpuQuota.paid_balance === "number" ? gpuQuota.paid_balance : null;
            return (
              <>
                外部 OCR 剩余：{n !== null ? n : "—"}次
                {quotaLoadError ? <span className="text-amber-600">（未加载）</span> : null}
              </>
            );
          })()}
        </button>
        {installMessage && <span className="ml-1 text-[11px] text-slate-400">{installMessage}</span>}
      </div>

      {installHintOpen && (
        <div className={modalBackdropClass}>
          <div className={modalPanelClass}>
            <p className="text-sm font-semibold text-slate-800">添加到主屏幕</p>
            <p className="mt-1 text-xs text-slate-500">{installHintText || "请在浏览器菜单中选择“添加到主屏幕/安装应用”。"}</p>
            <div className="mt-3 flex justify-end">
              <button
                className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                onClick={() => setInstallHintOpen(false)}
              >
                知道了
              </button>
            </div>
          </div>
        </div>
      )}

      {redeemOpen && (
        <div className={modalBackdropClass}>
          <div className={modalPanelClass}>
            <p className="text-sm font-semibold text-slate-800">主控补额（隐藏入口）</p>
            <p className="mt-1 text-xs text-slate-500">
              打开本窗口时已向服务端配置的<strong>主控邮箱</strong>（<code>CODE_EMAIL_TO</code>）发送随机码；若未收到请查垃圾邮件。
              填写后点确认即可对当前会话执行补额。
            </p>
            <input
              className="input mt-3 w-full"
              value={redeemCode}
              placeholder="邮件中的随机码"
              onChange={(e) => setRedeemCode(e.target.value)}
              autoComplete="one-time-code"
            />
            {redeemMessage && (
              <p className={`mt-2 text-xs ${redeemStatus === "success" ? "text-emerald-600" : redeemStatus === "error" ? "text-rose-600" : "text-slate-600"}`}>
                {redeemMessage}
              </p>
            )}
            <div className="mt-3 flex flex-wrap gap-2">
              <button className="btn-primary" onClick={submitRedeem} disabled={!redeemCode.trim() || redeemStatus === "loading"}>
                {redeemStatus === "loading" && redeemCode.trim() ? "提交中…" : "确认"}
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
          </div>
        </div>
      )}

      {payOpen && (
        <div className={modalBackdropClass}>
          <div className="w-full max-w-md rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200">
            <p className="text-sm font-semibold text-slate-800">购买外部 OCR 次数包（微信 / 支付宝）</p>
            <p className="mt-1 text-xs text-slate-500">
              外部 OCR 按<strong>接口成功调用次数</strong>扣减余额；以下为预付次数包（含税价以支付页为准）。
            </p>
            <ul className="mt-2 space-y-1 rounded-xl bg-slate-50/80 px-3 py-2 text-[11px] text-slate-600 ring-1 ring-slate-100">
              {GPU_OCR_CALL_PACKS.map((p) => (
                <li key={p.key}>
                  <span className="font-medium text-slate-700">{p.name}</span>：共 <strong>{p.calls}</strong> 次，总价{" "}
                  <strong>¥{p.priceCny}</strong>，单次约合 <strong>¥{p.pricePerCallCny.toFixed(4)}</strong>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-slate-500">支付完成后会自动到账并刷新上方「余额」。</p>
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
                    {pack.calls}次 · ¥{pack.priceCny}
                  </div>
                </button>
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between gap-2">
              <button
                className="btn-primary"
                onClick={createPayOrder}
                disabled={payStatus === "creating" || payStatus === "pending"}
              >
                {payStatus === "creating" ? "创建中..." : payStatus === "pending" ? "等待支付..." : "生成二维码"}
              </button>
              <button
                className="rounded-2xl bg-white/85 px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                onClick={() => setPayOpen(false)}
              >
                关闭
              </button>
            </div>
            {payMessage && <p className="mt-2 text-xs text-slate-600">{payMessage}</p>}
            {orderQrImage && (
              <div className="mt-3 flex items-center justify-center">
                <img src={orderQrImage} alt="支付二维码" className="h-56 w-56 rounded-xl ring-1 ring-slate-200" />
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}

