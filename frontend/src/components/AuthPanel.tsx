import { useEffect, useState } from "react";
import { API_BASE } from "../config/apiBase";
import { fetchLocalAuthProfile, setAccessToken, useAccessToken, type LocalAuthProfile } from "../lib/auth";
import { formatApiFetchError } from "../lib/fetchErrors";
import { withTenantHeaders } from "../hooks/useDocuments";
import ModalShell from "./ModalShell";

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
    if (!authModalOpen && !adminModalOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setAuthModalOpen(false);
        setAdminModalOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [adminModalOpen, authModalOpen]);

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

  const closeAuthModal = () => setAuthModalOpen(false);

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
    closeAuthModal();
    onAuthed();
  };

  const loadAdminUsers = async () => {
    if (!accessToken) return;
    setAdminLoading(true);
    setAdminMsg("");
    try {
      const response = await fetch(`${API_BASE}/admin/local-users`, {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "加载用户失败");
      }
      const data = await response.json();
      setAdminUsers(Array.isArray(data?.users) ? (data.users as AdminUser[]) : []);
    } catch (error) {
      setAdminMsg(formatApiFetchError(error, "加载用户失败"));
    } finally {
      setAdminLoading(false);
    }
  };

  const openAdminModal = async () => {
    setAdminModalOpen(true);
    await loadAdminUsers();
  };

  const updateAdminUserMode = async (userId: string, providerBillingMode: ProviderBillingMode) => {
    if (!accessToken) return;
    setAdminSavingUserId(userId);
    setAdminMsg("");
    try {
      const response = await fetch(`${API_BASE}/admin/local-users/${encodeURIComponent(userId)}/billing-mode`, {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ provider_billing_mode: providerBillingMode }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "保存失败");
      }
      const data = (await response.json()) as AdminUser;
      setAdminUsers((prev) =>
        prev.map((item) =>
          item.user_id === userId
            ? {
                ...item,
                provider_billing_mode: data.provider_billing_mode,
                effective_provider_billing_mode: data.effective_provider_billing_mode,
              }
            : item
        )
      );
      setAdminMsg("保存成功");
    } catch (error) {
      setAdminMsg(formatApiFetchError(error, "保存失败"));
    } finally {
      setAdminSavingUserId("");
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
      closeAuthModal();
      setMsg("登录成功");
      onAuthed();
    } catch (error) {
      setMsg(formatApiFetchError(error, "登录失败"));
    } finally {
      setLoading(false);
    }
  };

  const validateRegisterPasswords = (): boolean => {
    if (password.length < 8) {
      setMsg("密码至少 8 位");
      return false;
    }
    if (password !== passwordConfirm) {
      setMsg("两次输入的密码不一致");
      return false;
    }
    return true;
  };

  const requestRegisterCode = async () => {
    if (!validateRegisterPasswords()) return;

    setLoading(true);
    setMsg("");
    try {
      const response = await fetch(`${API_BASE}/auth/register/request-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error((await response.text()) || "发送失败");
      }

      setStep("code");
      if (data?.dev_code) setMsg(`开发模式验证码：${data.dev_code}`);
      else setMsg("验证码已发送，请检查邮箱。");
    } catch (error) {
      setMsg(formatApiFetchError(error, "发送验证码失败"));
    } finally {
      setLoading(false);
    }
  };

  const doRegister = async () => {
    if (!validateRegisterPasswords()) return;

    setLoading(true);
    setMsg("");
    try {
      const response = await fetch(`${API_BASE}/auth/register/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email, password, code }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "注册失败");
      }

      const data = await response.json();
      const token = String(data?.access_token || "").trim();
      if (!token) throw new Error("响应缺少 access_token");

      setAccessToken(token);
      closeAuthModal();

      const freeCallsRaw = data?.free_ocr_calls_granted ?? data?.free_ocr_pages_granted ?? 0;
      const freeCalls = typeof freeCallsRaw === "number" ? freeCallsRaw : Number(freeCallsRaw) || 0;
      if (freeCalls > 0) {
        setMsg(`注册成功，已赠送 ${freeCalls} 次外部 OCR 额度`);
      } else {
        setMsg("注册成功");
      }

      setStep("email");
      setCode("");
      setPasswordConfirm("");
      onAuthed();
    } catch (error) {
      setMsg(formatApiFetchError(error, "注册失败"));
    } finally {
      setLoading(false);
    }
  };

  const requestResetCode = async () => {
    setLoading(true);
    setMsg("");
    try {
      const response = await fetch(`${API_BASE}/auth/password/request-reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "发送失败");
      }
      setStep("code");
      if (data?.dev_code) setMsg(`开发模式验证码：${data.dev_code}`);
      else setMsg("如果该邮箱已注册，验证码会发送到你的邮箱。");
    } catch (error) {
      setMsg(formatApiFetchError(error, "发送重置验证码失败"));
    } finally {
      setLoading(false);
    }
  };

  const doResetPassword = async () => {
    if (password.length < 8) {
      setMsg("新密码至少 8 位");
      return;
    }
    if (password !== passwordConfirm) {
      setMsg("两次输入的新密码不一致");
      return;
    }

    setLoading(true);
    setMsg("");
    try {
      const response = await fetch(`${API_BASE}/auth/password/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...withTenantHeaders() },
        body: JSON.stringify({ email, password, code }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "重置密码失败");
      }
      const data = await response.json();
      const token = String(data?.access_token || "").trim();
      if (!token) throw new Error("响应缺少 access_token");

      setAccessToken(token);
      closeAuthModal();
      setMsg("密码已重置，并已自动登录");
      onAuthed();
    } catch (error) {
      setMsg(formatApiFetchError(error, "重置密码失败"));
    } finally {
      setLoading(false);
    }
  };

  if (loggedIn) {
    return (
      <>
        <div className="rounded-xl border border-slate-200 bg-white/90 px-3 py-2 text-sm shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <span className="text-slate-600">已登录</span>
            <div className="flex items-center gap-2">
              {profile?.is_admin ? (
                <button type="button" className="rounded-lg border border-slate-200 px-2 py-1 text-slate-700" onClick={openAdminModal}>
                  用户计费
                </button>
              ) : null}
              <button type="button" className="rounded-lg border border-slate-200 px-2 py-1 text-slate-700" onClick={logout}>
                退出
              </button>
            </div>
          </div>
          {profile?.is_admin ? (
            <p className="mt-2 text-xs text-slate-500">
              当前角色：管理员
              {profile.provider_billing_mode ? `，个人计费模式：${profile.provider_billing_mode}` : ""}
            </p>
          ) : null}
        </div>

        <ModalShell
          open={adminModalOpen}
          onClose={() => setAdminModalOpen(false)}
          panelClassName="relative my-auto w-full max-w-3xl max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200 sm:max-h-[calc(100dvh-3rem)]"
        >
          <div role="dialog" aria-modal="true" aria-labelledby="admin-billing-title">
            <div className="mb-3 flex items-center justify-between gap-2">
              <div>
                <p id="admin-billing-title" className="text-base font-semibold text-slate-900">
                  用户计费模式
                </p>
                <p className="mt-1 text-xs text-slate-500">管理员可以按用户切换本地计费或自托管直连计费。</p>
              </div>
              <button
                type="button"
                className="rounded-lg px-2 py-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                aria-label="关闭"
                onClick={() => setAdminModalOpen(false)}
              >
                ×
              </button>
            </div>

            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="text-xs text-slate-500">`default` 代表跟随全局环境变量，`internal` 代表走本地余额，`self_hosted` 代表直连你自己的上游 API。</p>
              <button
                type="button"
                className="rounded-lg border border-slate-200 px-2 py-1 text-sm text-slate-700"
                onClick={() => {
                  void loadAdminUsers();
                }}
                disabled={adminLoading}
              >
                刷新
              </button>
            </div>

            {adminMsg ? <p className="mb-3 text-xs text-slate-700">{adminMsg}</p> : null}

            <div className="space-y-3">
              {adminUsers.map((user) => (
                <div key={user.user_id} className="rounded-xl border border-slate-200 p-3">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-900">{user.email}</p>
                      <p className="mt-1 text-xs text-slate-500">
                        {user.is_admin ? "管理员" : "普通用户"} | 当前生效：{user.effective_provider_billing_mode}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <select
                        className="rounded-lg border border-slate-200 px-2 py-1 text-sm text-slate-700"
                        value={user.provider_billing_mode}
                        onChange={(event) => {
                          const nextMode = event.target.value as ProviderBillingMode;
                          setAdminUsers((prev) =>
                            prev.map((item) =>
                              item.user_id === user.user_id
                                ? {
                                    ...item,
                                    provider_billing_mode: nextMode,
                                  }
                                : item
                            )
                          );
                        }}
                        disabled={adminSavingUserId === user.user_id}
                      >
                        <option value="default">default</option>
                        <option value="internal">internal</option>
                        <option value="self_hosted">self_hosted</option>
                      </select>
                      <button
                        type="button"
                        className="rounded-lg border border-slate-200 px-3 py-1 text-sm text-slate-700"
                        onClick={() => {
                          void updateAdminUserMode(user.user_id, user.provider_billing_mode);
                        }}
                        disabled={adminSavingUserId === user.user_id}
                      >
                        {adminSavingUserId === user.user_id ? "保存中..." : "保存"}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
              {!adminLoading && adminUsers.length === 0 ? <p className="text-sm text-slate-500">暂无用户</p> : null}
              {adminLoading ? <p className="text-sm text-slate-500">加载中...</p> : null}
            </div>
          </div>
        </ModalShell>
      </>
    );
  }

  const formInner = (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex gap-2">
          <button
            type="button"
            className={`rounded-lg px-2 py-1 ${mode === "login" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => switchMode("login")}
          >
            登录
          </button>
          <button
            type="button"
            className={`rounded-lg px-2 py-1 ${mode === "register" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => switchMode("register")}
          >
            注册
          </button>
          <button
            type="button"
            className={`rounded-lg px-2 py-1 ${mode === "reset" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => switchMode("reset")}
          >
            找回密码
          </button>
        </div>
        <button
          type="button"
          className="rounded-lg px-2 py-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          aria-label="关闭"
          onClick={closeAuthModal}
        >
          ×
        </button>
      </div>

      <label className="block text-xs text-slate-500">邮箱</label>
      <input className="input mb-2 w-full" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />

      <label className="block text-xs text-slate-500">{mode === "reset" ? "新密码（至少 8 位）" : "密码（至少 8 位）"}</label>
      <input
        className="input mb-2 w-full"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete={mode === "login" ? "current-password" : "new-password"}
      />

      {mode !== "login" && (
        <>
          <label className="block text-xs text-slate-500">{mode === "reset" ? "确认新密码" : "确认密码"}</label>
          <input
            className="input mb-2 w-full"
            type="password"
            value={passwordConfirm}
            onChange={(e) => setPasswordConfirm(e.target.value)}
            autoComplete="new-password"
          />
        </>
      )}

      {(mode === "register" || mode === "reset") && step === "code" && (
        <>
          <label className="block text-xs text-slate-500">验证码</label>
          <input className="input mb-2 w-full" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />
        </>
      )}

      {msg && <p className="mb-2 text-xs text-slate-700">{msg}</p>}

      <div className="flex flex-wrap gap-2">
        {mode === "login" ? (
          <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={doLogin}>
            {loading ? "处理中..." : "登录"}
          </button>
        ) : mode === "register" ? (
          step === "email" ? (
            <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={requestRegisterCode}>
              {loading ? "处理中..." : "发送验证码"}
            </button>
          ) : (
            <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={doRegister}>
              {loading ? "处理中..." : "完成注册"}
            </button>
          )
        ) : step === "email" ? (
          <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={requestResetCode}>
            {loading ? "处理中..." : "发送重置验证码"}
          </button>
        ) : (
          <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={doResetPassword}>
            {loading ? "处理中..." : "重置密码"}
          </button>
        )}
      </div>

      {mode === "login" && (
        <button type="button" className="mt-3 text-xs text-violet-600 hover:text-violet-700" onClick={() => switchMode("reset")}>
          忘记密码？
        </button>
      )}
    </>
  );

  return (
    <>
      <button
        type="button"
        className="rounded-xl border border-violet-200 bg-violet-50/90 px-4 py-2 text-sm font-semibold text-violet-800 shadow-sm ring-1 ring-violet-100 transition hover:bg-violet-100/90"
        onClick={() => setAuthModalOpen(true)}
      >
        登录 / 注册
      </button>

      <ModalShell
        open={authModalOpen}
        onClose={closeAuthModal}
        panelClassName="relative my-auto w-full max-w-sm max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200 sm:max-h-[calc(100dvh-3rem)]"
      >
        <div role="dialog" aria-modal="true" aria-labelledby="auth-modal-title">
          <p id="auth-modal-title" className="sr-only">
            登录、注册或找回密码
          </p>
          {formInner}
        </div>
      </ModalShell>
    </>
  );
}
