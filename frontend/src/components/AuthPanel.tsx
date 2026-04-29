import { useEffect, useState } from "react";
import { API_BASE } from "../config/apiBase";
import { fetchLocalAuthProfile, setAccessToken, useAccessToken, type LocalAuthProfile } from "../lib/auth";
import { formatApiFetchError } from "../lib/fetchErrors";
import { withTenantHeaders } from "../hooks/useDocuments";
import { formatPayChannel, type PayChannel } from "../config/payProducts";
import ModalShell from "./ModalShell";
import { User, CreditCard, LogOut, Settings, Wallet, X, ChevronRight } from "lucide-react";

type Props = {
  onAuthed: () => void;
};

type Mode = "login" | "register" | "reset";
type Step = "email" | "code";
type ProviderBillingMode = "default" | "internal" | "self_hosted";

type AdminUser = {
  user_id: string;
  email: string;
  is_admin: boolean;
  provider_billing_mode: ProviderBillingMode;
  effective_provider_billing_mode: "internal" | "self_hosted";
  created_at: string;
};

export default function AuthPanel({ onAuthed }: Props) {
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [adminModalOpen, setAdminModalOpen] = useState(false);
  const [rechargeModalOpen, setRechargeModalOpen] = useState(false);
  const [rechargeType, setRechargeType] = useState<"ocr_calls" | "glm_ocr_tokens" | "embedding_tokens">("ocr_calls");
  const [rechargePack, setRechargePack] = useState<string>("A");
  const [rechargeChannel, setRechargeChannel] = useState<PayChannel>("wechat_native");
  const [availablePayProviders, setAvailablePayProviders] = useState<string[]>(["xpay", "paypal"]);
  const [payProviderChannels, setPayProviderChannels] = useState<Record<string, PayChannel[]>>({
    xpay: ["wechat_native", "alipay_qr"],
    paypal: ["paypal"],
  });
  const [payProviderHealth, setPayProviderHealth] = useState<Record<string, { available?: boolean; auto_confirm?: boolean; reason?: string }>>({});
  const [payMessage, setPayMessage] = useState("");
  const [payQrImage, setPayQrImage] = useState("");
  const [payPageUrl, setPayPageUrl] = useState("");
  const [payOrderNo, setPayOrderNo] = useState("");
  const [payAmount, setPayAmount] = useState<number | null>(null);
  const [payOrderProvider, setPayOrderProvider] = useState("");
  const [payOrderStatus, setPayOrderStatus] = useState<"idle" | "pending" | "paid" | "failed">("idle");
  
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<Step>("email");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [profile, setProfile] = useState<LocalAuthProfile | null>(null);
  const [adminUsers, setAdminUsers] = useState<AdminUser[]>([]);
  const [adminLoading, setAdminLoading] = useState(false);
  const [adminSavingUserId, setAdminSavingUserId] = useState("");
  const [adminMsg, setAdminMsg] = useState("");

  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);

  useEffect(() => {
    let cancelled = false;
    const loadProfile = async () => {
      if (!accessToken) {
        setProfile(null);
        setAdminUsers([]);
        setAdminModalOpen(false);
        return;
      }
      const nextProfile = await fetchLocalAuthProfile(accessToken);
      if (!cancelled) {
        setProfile(nextProfile);
      }
    };
    void loadProfile();
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  const normalizePaymentUrl = (rawUrl: string): string => {
    if (!rawUrl || typeof window === "undefined") return rawUrl;
    try {
      const currentOrigin = window.location.origin;
      const apiUrl = new URL(API_BASE, currentOrigin);
      const parsed = new URL(rawUrl, currentOrigin);
      if (parsed.origin === apiUrl.origin) return parsed.toString();
      if (/^https?:$/.test(parsed.protocol) && parsed.pathname.startsWith("/gpu/")) {
        return `${apiUrl.origin}${API_BASE.replace(/\/$/, "")}${parsed.pathname}${parsed.search}`;
      }
      return parsed.toString();
    } catch {
      return rawUrl;
    }
  };

  const payProviderForChannel = (channel: PayChannel) => {
    if (channel === "paypal") return "paypal";
    const providerCandidates = availablePayProviders.filter((provider) => provider !== "paypal");
    const autoProvider = providerCandidates.find((provider) => {
      const meta = payProviderHealth[provider];
      return meta?.available !== false && meta?.auto_confirm === true;
    });
    if (autoProvider) return autoProvider;
    const availableProvider = providerCandidates.find((provider) => payProviderHealth[provider]?.available !== false);
    return availableProvider || providerCandidates[0] || "xpay";
  };

  const isRechargeChannelEnabled = (channel: PayChannel) => {
    const provider = payProviderForChannel(channel);
    const channels = payProviderChannels[provider];
    return availablePayProviders.includes(provider) && Array.isArray(channels) && channels.includes(channel);
  };

  useEffect(() => {
    if (!rechargeModalOpen) return;
    let cancelled = false;
    const loadPayConfig = async () => {
      try {
        const response = await fetch(`${API_BASE}/gpu/ocr/pay/config`, {
          headers: withTenantHeaders(),
          credentials: "include",
        });
        if (!response.ok) return;
        const data = await response.json();
        if (cancelled) return;
        const providers = Array.isArray(data?.providers)
          ? data.providers.filter((item: unknown): item is string => typeof item === "string" && item.length > 0)
          : [];
        const channelMap: Record<string, PayChannel[]> =
          data?.provider_channels && typeof data.provider_channels === "object" ? data.provider_channels : {};
        const healthMap =
          data?.provider_health && typeof data.provider_health === "object"
            ? (data.provider_health as Record<string, { available?: boolean; auto_confirm?: boolean; reason?: string }>)
            : {};
        if (providers.length > 0) {
          setAvailablePayProviders(providers);
        }
        if (Object.keys(channelMap).length > 0) {
          setPayProviderChannels(channelMap);
        }
        setPayProviderHealth(healthMap);
      } catch {
        // 支付配置失败时保留默认 xpay/paypal 入口，具体下单错误会在按钮处提示。
      }
    };
    void loadPayConfig();
    return () => {
      cancelled = true;
    };
  }, [rechargeModalOpen]);

  useEffect(() => {
    if (!rechargeModalOpen || !payOrderNo || !loggedIn) return;
    const providerMeta = payProviderHealth[payOrderProvider];
    if (!providerMeta?.auto_confirm || payOrderStatus === "paid" || payOrderStatus === "failed") return;
    let cancelled = false;
    let timer: number | null = null;

    const pollOnce = async () => {
      try {
        const response = await fetch(`${API_BASE}/gpu/ocr/pay/order/${encodeURIComponent(payOrderNo)}`, {
          headers: {
            Authorization: `Bearer ${accessToken}`,
            ...withTenantHeaders(),
          },
        });
        if (!response.ok || cancelled) return;
        const data = await response.json();
        if (cancelled) return;
        const status = String(data?.status || "pending").toLowerCase();
        if (status === "paid") {
          setPayOrderStatus("paid");
          setPayMessage("支付成功，额度已自动到账。");
          return;
        }
        if (status === "failed" || status === "refunded") {
          setPayOrderStatus("failed");
          setPayMessage(`订单状态：${status}`);
          return;
        }
        setPayOrderStatus("pending");
        timer = window.setTimeout(() => {
          void pollOnce();
        }, 3000);
      } catch {
        timer = window.setTimeout(() => {
          void pollOnce();
        }, 5000);
      }
    };

    void pollOnce();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [accessToken, loggedIn, payOrderNo, payOrderProvider, payOrderStatus, payProviderHealth, rechargeModalOpen]);

  const switchMode = (next: Mode) => {
    setMode(next);
    setStep("email");
    setCode("");
    setMsg("");
    if (next !== "register") {
      setPasswordConfirm("");
    }
  };

  const logout = () => {
    setAccessToken(null);
    setProfile(null);
    setAdminUsers([]);
    setAdminModalOpen(false);
    setMsg("已退出登录");
    setAuthModalOpen(false);
    onAuthed();
  };

  const handleRecharge = async () => {
    if (!rechargePack) {
      alert("请选择充值套餐");
      return;
    }
    setLoading(true);
    setPayMessage("");
    setPayQrImage("");
    setPayPageUrl("");
    setPayOrderNo("");
    setPayAmount(null);
    setPayOrderProvider("");
    setPayOrderStatus("idle");
    try {
      const provider = payProviderForChannel(rechargeChannel);
      const resp = await fetch(`${API_BASE}/gpu/ocr/pay/order/create`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
          ...withTenantHeaders()
        },
        body: JSON.stringify({ 
          provider,
          product_type: rechargeType,
          product_key: rechargePack,
          channel: rechargeChannel
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "创建订单失败");
      const nextQr = typeof data?.qr_image_url === "string" ? normalizePaymentUrl(data.qr_image_url) : "";
      const nextPayPageUrl = typeof data?.pay_page_url === "string" ? normalizePaymentUrl(data.pay_page_url) : "";
      setPayQrImage(nextQr);
      setPayPageUrl(nextPayPageUrl);
      setPayOrderNo(typeof data?.order_no === "string" ? data.order_no : "");
      setPayAmount(typeof data?.amount_cny === "number" ? data.amount_cny : null);
      setPayOrderProvider(typeof data?.provider === "string" ? data.provider : provider);
      setPayOrderStatus("pending");
      setPayMessage(
        typeof data?.pay_hint === "string" && data.pay_hint
          ? data.pay_hint
          : rechargeChannel === "paypal"
            ? "PayPal 支付页已生成，请打开支付页完成付款。"
            : `请使用${formatPayChannel(rechargeChannel)}扫码付款。`
      );
      if (!nextQr && !nextPayPageUrl) {
        setPayMessage("订单已创建，但没有返回二维码或支付链接，请检查支付配置。");
      }
    } catch (e) {
      setPayOrderStatus("failed");
      setPayMessage(formatApiFetchError(e, "请求失败"));
    } finally {
      setLoading(false);
    }
  };

  const doLogin = async () => {
    setLoading(true);
    setMsg("");
    try {
      const response = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "登录失败");
      }
      const data = await response.json();
      const token = String(data?.access_token || "").trim();
      if (!token) throw new Error("响应缺少 access_token");
      setAccessToken(token);
      setAuthModalOpen(false);
      onAuthed();
    } catch (error) {
      setMsg(formatApiFetchError(error, "登录失败"));
    } finally {
      setLoading(false);
    }
  };

  const readResponseError = async (response: Response, fallback: string) => {
    const text = await response.text();
    if (!text) return fallback;
    try {
      const data = JSON.parse(text) as { detail?: unknown; message?: unknown };
      const detail = data.detail ?? data.message;
      if (typeof detail === "string" && detail.trim()) return detail;
    } catch {
      // Plain-text errors are still useful for local debugging.
    }
    return text;
  };

  const requestRegisterCode = async () => {
    setMsg("");
    if (!email.trim() || !email.includes("@")) {
      setMsg("请先填写有效邮箱");
      return;
    }
    if (password.length < 8) {
      setMsg("密码至少 8 位");
      return;
    }
    if (password !== passwordConfirm) {
      setMsg("两次输入的密码不一致");
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/auth/register/request-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email }),
      });
      if (!response.ok) {
        throw new Error(await readResponseError(response, "验证码发送失败"));
      }
      const data = await response.json();
      setStep("code");
      setMsg(
        data?.dev_code
          ? `验证码已生成：${data.dev_code}`
          : "验证码已发送，请检查邮箱收件箱或垃圾邮件。"
      );
    } catch (error) {
      setMsg(formatApiFetchError(error, "验证码发送失败"));
    } finally {
      setLoading(false);
    }
  };

  const completeRegister = async () => {
    setMsg("");
    if (!code.trim()) {
      setMsg("请输入邮箱验证码");
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/auth/register/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email, password, code }),
      });
      if (!response.ok) {
        throw new Error(await readResponseError(response, "注册失败"));
      }
      const data = await response.json();
      const token = String(data?.access_token || "").trim();
      if (!token) throw new Error("注册成功但响应缺少 access_token");
      setAccessToken(token);
      setAuthModalOpen(false);
      onAuthed();
    } catch (error) {
      setMsg(formatApiFetchError(error, "注册失败"));
    } finally {
      setLoading(false);
    }
  };

  if (loggedIn) {
    return (
      <div className="flex items-center gap-3">
        {/* User Badge - Dopamine Style */}
        <div 
          onClick={() => setAuthModalOpen(true)}
          className="neo-box-sm bg-white hover:bg-yellow-50 px-4 py-2 flex items-center gap-3 cursor-pointer group"
        >
          <div className="neo-box-sm bg-blue-400 p-1 group-hover:rotate-12 transition-transform">
             <User size={18} className="text-white" />
          </div>
          <div className="hidden sm:block">
            <p className="text-[10px] font-black uppercase tracking-widest opacity-40 leading-none mb-1">已认证</p>
            <p className="text-xs font-black truncate max-w-[120px]">{profile?.user_id?.slice(0, 8) || "用户"}</p>
          </div>
        </div>

        {/* Quick Actions */}
        <button 
          onClick={() => setRechargeModalOpen(true)}
          className="neo-button-sm bg-green-400 text-slate-900 hidden md:flex items-center gap-2"
        >
          <CreditCard size={14} />
          充值
        </button>

        {/* User Menu Modal */}
        <ModalShell
          open={authModalOpen}
          onClose={() => setAuthModalOpen(false)}
          panelClassName="relative my-auto w-full max-w-sm neo-box bg-white p-8"
        >
          <div className="flex flex-col gap-6">
            <div className="flex justify-between items-center bg-yellow-400 -m-8 p-8 mb-4 border-b-4 border-slate-900">
               <div className="flex items-center gap-3">
                 <div className="neo-box-sm bg-white p-2"><User /></div>
                 <h2 className="text-xl font-black uppercase">个人中心</h2>
               </div>
               <button onClick={() => setAuthModalOpen(false)}><X /></button>
            </div>

            <div className="space-y-4">
               <div className="neo-box-sm bg-slate-50 p-4">
                  <p className="text-[10px] font-black uppercase opacity-40 mb-1">用户ID</p>
                  <p className="font-black text-sm">{profile?.user_id || "暂无"}</p>
               </div>

               <div className="grid grid-cols-2 gap-3">
                  <button 
                    onClick={() => { setAuthModalOpen(false); setRechargeModalOpen(true); }}
                    className="neo-button-sm bg-green-400 flex flex-col items-center gap-2 p-4"
                  >
                    <Wallet />
                    <span>充值中心</span>
                  </button>
                  <button 
                    onClick={() => { setAuthModalOpen(false); setAdminModalOpen(true); }}
                    className={`neo-button-sm flex flex-col items-center gap-2 p-4 ${profile?.is_admin ? 'bg-blue-400' : 'bg-slate-100 opacity-50 cursor-not-allowed'}`}
                    disabled={!profile?.is_admin}
                  >
                    <Settings />
                    <span>后台管理</span>
                  </button>
               </div>

               <button 
                onClick={logout}
                className="neo-button w-full bg-pink-500 text-white flex items-center justify-center gap-3"
               >
                 <LogOut />
                 <span>退出登录</span>
               </button>
            </div>
          </div>
        </ModalShell>

        {/* Recharge Modal */}
        <ModalShell
          open={rechargeModalOpen}
          onClose={() => setRechargeModalOpen(false)}
          panelClassName="relative my-auto w-full max-w-sm neo-box bg-white p-8"
        >
          <div className="flex flex-col gap-6">
            <div className="flex justify-between items-center bg-green-400 -m-8 p-8 mb-4 border-b-4 border-slate-900 text-slate-900">
               <div className="flex items-center gap-3">
                 <div className="neo-box-sm bg-white p-2"><Wallet /></div>
                 <h2 className="text-xl font-black uppercase">账户充值</h2>
               </div>
               <button onClick={() => setRechargeModalOpen(false)}><X /></button>
            </div>

            <div className="space-y-6">
               <p className="text-sm font-bold text-slate-600">选择计费类型和套餐：</p>
               
               {/* 计费类型选择 */}
               <div className="flex gap-2 bg-slate-100 p-1 neo-box-sm">
                 <button 
                  className={`flex-1 neo-button-sm py-2 ${rechargeType === 'ocr_calls' ? 'bg-yellow-400 shadow-none' : 'bg-transparent border-0 shadow-none'}`}
                  onClick={() => { setRechargeType('ocr_calls'); setRechargePack('A'); setPayMessage(""); setPayQrImage(""); setPayPageUrl(""); }}
                 >外部OCR调用</button>
                 <button 
                  className={`flex-1 neo-button-sm py-2 ${rechargeType === 'glm_ocr_tokens' ? 'bg-blue-400 text-white shadow-none' : 'bg-transparent border-0 shadow-none'}`}
                  onClick={() => { setRechargeType('glm_ocr_tokens'); setRechargePack('T1'); setPayMessage(""); setPayQrImage(""); setPayPageUrl(""); }}
                 >GLM OCR Tokens</button>
                 <button 
                  className={`flex-1 neo-button-sm py-2 ${rechargeType === 'embedding_tokens' ? 'bg-green-400 text-white shadow-none' : 'bg-transparent border-0 shadow-none'}`}
                  onClick={() => { setRechargeType('embedding_tokens'); setRechargePack('S1'); setPayMessage(""); setPayQrImage(""); setPayPageUrl(""); }}
                 >Embedding Tokens</button>
               </div>

               <div className="space-y-2">
                 <p className="text-xs font-black uppercase opacity-60">支付方式：</p>
                 <div className="grid grid-cols-3 gap-2">
                   {([
                     ["wechat_native", "微信"],
                     ["alipay_qr", "支付宝"],
                     ["paypal", "PayPal"],
                   ] as [PayChannel, string][]).map(([channel, label]) => (
                     <button
                       key={channel}
                       type="button"
                       onClick={() => { setRechargeChannel(channel); setPayMessage(""); setPayQrImage(""); setPayPageUrl(""); }}
                       disabled={!isRechargeChannelEnabled(channel) || loading}
                       className={`neo-button-sm py-2 ${
                         rechargeChannel === channel
                           ? "bg-slate-900 text-white"
                           : isRechargeChannelEnabled(channel)
                             ? "bg-white"
                             : "bg-slate-100 opacity-40 cursor-not-allowed"
                       }`}
                     >
                       {label}
                     </button>
                   ))}
                 </div>
                 {rechargeChannel !== "paypal" && (() => {
                   const provider = payProviderForChannel(rechargeChannel);
                   const meta = payProviderHealth[provider];
                   if (!meta) return null;
                   return (
                     <p className={`text-[10px] font-bold ${meta.auto_confirm ? "text-emerald-700" : "text-amber-700"}`}>
                       {meta.auto_confirm ? "当前将优先走自动到账通道" : `当前为手动确认通道${meta.reason ? `：${meta.reason}` : ""}`}
                     </p>
                   );
                 })()}
               </div>

               {/* 套餐包选择 */}
               <div className="space-y-4">
                 <p className="text-xs font-black uppercase opacity-60">选择套餐：</p>
                 
                 {rechargeType === 'ocr_calls' && (
                   <div className="grid grid-cols-3 gap-3">
                     {[
                       {key: 'A', calls: 500, price: 9.9},
                       {key: 'B', calls: 2000, price: 29.9},
                       {key: 'C', calls: 5000, price: 59.9}
                     ].map(pack => (
                       <button
                         key={pack.key}
                         onClick={() => setRechargePack(pack.key)}
                         className={`neo-button-sm p-4 flex flex-col items-center ${rechargePack === pack.key ? 'bg-yellow-400' : 'bg-white'}`}
                       >
                         <span className="text-lg font-black">{pack.key}包</span>
                         <span className="text-xs opacity-70">{pack.calls}次</span>
                         <span className="text-sm font-bold mt-1">¥{pack.price}</span>
                       </button>
                     ))}
                   </div>
                 )}
                 
                 {rechargeType === 'glm_ocr_tokens' && (
                   <div className="grid grid-cols-3 gap-3">
                     {[
                       {key: 'T1', tokens: 20000, price: 19.9},
                       {key: 'T2', tokens: 80000, price: 59.9},
                       {key: 'T3', tokens: 200000, price: 129.9}
                     ].map(pack => (
                       <button
                         key={pack.key}
                         onClick={() => setRechargePack(pack.key)}
                         className={`neo-button-sm p-4 flex flex-col items-center ${rechargePack === pack.key ? 'bg-blue-400 text-white' : 'bg-white'}`}
                       >
                         <span className="text-lg font-black">{pack.key}包</span>
                         <span className="text-xs opacity-70">{pack.tokens.toLocaleString()} tokens</span>
                         <span className="text-sm font-bold mt-1">¥{pack.price}</span>
                       </button>
                     ))}
                   </div>
                 )}
                 
                 {rechargeType === 'embedding_tokens' && (
                   <div className="grid grid-cols-3 gap-3">
                     {[
                       {key: 'S1', tokens: 10000, price: 19.9},
                       {key: 'S2', tokens: 40000, price: 59.9},
                       {key: 'S3', tokens: 90000, price: 129.9}
                     ].map(pack => (
                       <button
                         key={pack.key}
                         onClick={() => setRechargePack(pack.key)}
                         className={`neo-button-sm p-4 flex flex-col items-center ${rechargePack === pack.key ? 'bg-green-400 text-white' : 'bg-white'}`}
                       >
                         <span className="text-lg font-black">{pack.key}包</span>
                         <span className="text-xs opacity-70">{pack.tokens.toLocaleString()} tokens</span>
                         <span className="text-sm font-bold mt-1">¥{pack.price}</span>
                       </button>
                     ))}
                   </div>
                 )}
                 
                 {/* 套餐描述 */}
                 <div className="neo-box-sm bg-slate-50 p-3 text-xs">
                   {rechargeType === 'ocr_calls' && (
                     <p>• <strong>外部OCR调用次数</strong>：用于简单版式图片的OCR识别</p>
                   )}
                   {rechargeType === 'glm_ocr_tokens' && (
                     <p>• <strong>GLM OCR Tokens</strong>：用于复杂版式文档的智能OCR识别（GLM模型）</p>
                   )}
                   {rechargeType === 'embedding_tokens' && (
                     <p>• <strong>Embedding Tokens</strong>：用于文档向量化和语义检索（Embedding-3模型）</p>
                   )}
                 </div>
               </div>

               <button 
                onClick={handleRecharge}
                disabled={loading}
                className="neo-button w-full bg-slate-900 text-white flex items-center justify-center gap-3 p-4"
               >
                 {loading ? "处理中..." : rechargeChannel === "paypal" ? "生成 PayPal 支付页" : "生成收款码"}
                 <ChevronRight />
               </button>
               {payMessage && (
                 <div className="neo-box-sm bg-yellow-100 p-3 text-xs font-bold leading-relaxed">
                   {payMessage}
                 </div>
               )}
               {payAmount != null && (
                 <p className="text-xs font-black text-rose-600">实付金额：¥{payAmount.toFixed(2)}</p>
               )}
               {payOrderNo && <p className="text-[10px] font-bold opacity-50">订单号：{payOrderNo}</p>}
               {payOrderStatus === "pending" && payProviderHealth[payOrderProvider]?.auto_confirm && (
                 <p className="text-[10px] font-bold text-emerald-700">正在等待自动到账确认...</p>
               )}
               {payQrImage && (
                 <div className="flex justify-center">
                   <img src={payQrImage} alt="支付二维码" className="h-56 w-56 rounded-xl border-4 border-slate-900 bg-white object-contain p-2" />
                 </div>
               )}
               {payPageUrl && (
                 <button
                  type="button"
                  onClick={() => window.open(payPageUrl, "_blank", "noopener,noreferrer")}
                  className="neo-button w-full bg-blue-400 text-white p-3 font-black"
                 >
                   打开支付页
                 </button>
               )}
            </div>
          </div>
        </ModalShell>
      </div>
    );
  }

  return (
    <>
      <button
        type="button"
        className="neo-button bg-pink-400 text-white flex items-center gap-3"
        onClick={() => setAuthModalOpen(true)}
      >
        <User size={20} />
        <span className="font-black uppercase tracking-widest">登录 / 注册</span>
      </button>

      <ModalShell
        open={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        panelClassName="relative my-auto w-full max-w-sm neo-box bg-white p-8"
      >
        <div className="flex flex-col gap-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[0.2em] opacity-40">Account</p>
              <h2 className="text-2xl font-black">{mode === "register" ? "邮箱注册" : "登录"}</h2>
            </div>
            <button type="button" onClick={() => setAuthModalOpen(false)} className="neo-button-sm bg-white p-2">
              <X size={18} />
            </button>
          </div>

          <div className="flex gap-2 bg-slate-100 p-1 neo-box-sm">
             <button 
              type="button"
              className={`flex-1 neo-button-sm py-2 ${mode === 'login' ? 'bg-yellow-400 shadow-none' : 'bg-transparent border-0 shadow-none'}`}
              onClick={() => switchMode('login')}
             >登录</button>
             <button 
              type="button"
              className={`flex-1 neo-button-sm py-2 ${mode === 'register' ? 'bg-pink-400 text-white shadow-none' : 'bg-transparent border-0 shadow-none'}`}
              onClick={() => switchMode('register')}
             >注册</button>
          </div>

          <div className="space-y-4">
             <div>
               <label className="text-[10px] font-black uppercase opacity-60 ml-1">邮箱</label>
               <input 
                className="neo-input w-full" 
                value={email} 
                onChange={e => setEmail(e.target.value)}
                placeholder="you@domain.com"
               />
             </div>
             <div>
               <label className="text-[10px] font-black uppercase opacity-60 ml-1">密码</label>
               <input 
                className="neo-input w-full" 
                type="password"
                value={password} 
                onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
               />
             </div>
             {mode === "register" && (
               <>
                 <div>
                   <label className="text-[10px] font-black uppercase opacity-60 ml-1">确认密码</label>
                   <input
                    className="neo-input w-full"
                    type="password"
                    value={passwordConfirm}
                    onChange={e => setPasswordConfirm(e.target.value)}
                    placeholder="再次输入密码"
                   />
                 </div>
                 {step === "code" && (
                   <div>
                     <label className="text-[10px] font-black uppercase opacity-60 ml-1">邮箱验证码</label>
                     <input
                      className="neo-input w-full"
                      value={code}
                      onChange={e => setCode(e.target.value)}
                      placeholder="6 位验证码"
                      inputMode="numeric"
                     />
                   </div>
                 )}
               </>
             )}
             
             {msg && <div className="neo-box-sm bg-yellow-100 p-3 text-[10px] font-black">{msg}</div>}

             <button
              type="button"
              onClick={mode === "login" ? doLogin : step === "email" ? requestRegisterCode : completeRegister}
              disabled={loading}
              className={`neo-button w-full p-4 font-black ${mode === "register" ? "bg-pink-400 text-white" : "bg-blue-400 text-white"}`}
             >
               {loading
                 ? "处理中..."
                 : mode === "login"
                   ? "进入工作区"
                   : step === "email"
                     ? "发送邮箱验证码"
                     : "完成注册并登录"}
             </button>
             {mode === "register" && step === "code" && (
               <button
                type="button"
                className="w-full text-xs font-black underline opacity-70 hover:opacity-100"
                onClick={() => {
                  setStep("email");
                  setCode("");
                  setMsg("");
                }}
               >
                 修改邮箱或重新发送验证码
               </button>
             )}
          </div>
        </div>
      </ModalShell>
    </>
  );
}
