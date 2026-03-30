import { useEffect, useState } from "react";
import { API_BASE } from "../config/apiBase";
import { setAccessToken, useAccessToken } from "../lib/auth";
import { formatApiFetchError } from "../lib/fetchErrors";
import { withTenantHeaders } from "../hooks/useDocuments";
import ModalShell from "./ModalShell";

type Props = {
  onAuthed: () => void;
};

export default function AuthPanel({ onAuthed }: Props) {
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);

  useEffect(() => {
    if (!authModalOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAuthModalOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [authModalOpen]);

  const closeModal = () => setAuthModalOpen(false);

  const logout = () => {
    setAccessToken(null);
    setMsg("已退出登录");
    closeModal();
    onAuthed();
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
      closeModal();
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

  const requestCode = async () => {
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
      closeModal();

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

  if (loggedIn) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white/90 px-3 py-2 text-sm shadow-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="text-slate-600">已登录</span>
          <button type="button" className="rounded-lg border border-slate-200 px-2 py-1 text-slate-700" onClick={logout}>
            退出
          </button>
        </div>
      </div>
    );
  }

  const formInner = (
    <>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex gap-2">
          <button
            type="button"
            className={`rounded-lg px-2 py-1 ${mode === "login" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => {
              setMode("login");
              setMsg("");
              setPasswordConfirm("");
            }}
          >
            登录
          </button>
          <button
            type="button"
            className={`rounded-lg px-2 py-1 ${mode === "register" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => {
              setMode("register");
              setStep("email");
              setMsg("");
              setPasswordConfirm("");
            }}
          >
            注册
          </button>
        </div>
        <button
          type="button"
          className="rounded-lg px-2 py-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
          aria-label="关闭"
          onClick={closeModal}
        >
          ×
        </button>
      </div>

      <label className="block text-xs text-slate-500">邮箱</label>
      <input className="input mb-2 w-full" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />

      <label className="block text-xs text-slate-500">密码（至少 8 位）</label>
      <input
        className="input mb-2 w-full"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        autoComplete={mode === "login" ? "current-password" : "new-password"}
      />

      {mode === "register" && (
        <>
          <label className="block text-xs text-slate-500">确认密码</label>
          <input
            className="input mb-2 w-full"
            type="password"
            value={passwordConfirm}
            onChange={(e) => setPasswordConfirm(e.target.value)}
            autoComplete="new-password"
          />
        </>
      )}

      {mode === "register" && step === "code" && (
        <>
          <label className="block text-xs text-slate-500">验证码</label>
          <input className="input mb-2 w-full" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" />
        </>
      )}

      {msg && <p className="mb-2 text-xs text-slate-700">{msg}</p>}

      <div className="flex flex-wrap gap-2">
        {mode === "login" ? (
          <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={doLogin}>
            {loading ? "..." : "登录"}
          </button>
        ) : step === "email" ? (
          <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={requestCode}>
            {loading ? "..." : "发送验证码"}
          </button>
        ) : (
          <button type="button" disabled={loading} className="btn-primary disabled:opacity-50" onClick={doRegister}>
            {loading ? "..." : "完成注册"}
          </button>
        )}
      </div>
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
        onClose={closeModal}
        panelClassName="relative my-auto w-full max-w-sm max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200 sm:max-h-[calc(100dvh-3rem)]"
      >
        <div role="dialog" aria-modal="true" aria-labelledby="auth-modal-title">
          <p id="auth-modal-title" className="sr-only">
            登录或注册
          </p>
          {formInner}
        </div>
      </ModalShell>
    </>
  );
}
