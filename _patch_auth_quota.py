from pathlib import Path

# App.tsx
p = Path("frontend/src/App.tsx")
t = p.read_text(encoding="utf-8")
t = t.replace(
    "  const [authLocalEnabled, setAuthLocalEnabled] = useState(false);",
    "  /** 默认 true：避免 /health 失败时整站不显示登录/注册；拉取成功后再以服务端为准 */\n  const [authLocalEnabled, setAuthLocalEnabled] = useState(true);",
)
t = t.replace(
    """        const data = await res.json();
        setAuthLocalEnabled(!!data?.auth_local_jwt_enabled);""",
    """        const data = await res.json();
        if (typeof data?.auth_local_jwt_enabled === "boolean") {
          setAuthLocalEnabled(data.auth_local_jwt_enabled);
        }""",
)
t = t.replace("<GpuQuotaWidget />", "<GpuQuotaWidget authSession={authSession} />")
p.write_text(t, encoding="utf-8")
print("App.tsx ok")

# GpuQuotaWidget.tsx
g = Path("frontend/src/components/GpuQuotaWidget.tsx")
s = g.read_text(encoding="utf-8")

if "getAccessToken" not in s.split("\n")[0:5]:
    s = s.replace(
        'import { formatApiFetchError } from "../lib/fetchErrors";',
        'import { formatApiFetchError } from "../lib/fetchErrors";\nimport { getAccessToken } from "../hooks/useDocuments";',
    )

if "type Props" not in s and "authSession" not in s[:800]:
    s = s.replace(
        "export default function GpuQuotaWidget() {",
        "type GpuQuotaWidgetProps = { authSession?: number };\n\nexport default function GpuQuotaWidget({ authSession = 0 }: GpuQuotaWidgetProps) {",
    )

# Replace useEffect for refreshQuota to depend on authSession
old_effect = """  useEffect(() => {
    void refreshQuota();
  }, []);"""

new_effect = """  useEffect(() => {
    if (!getAccessToken()) {
      setGpuQuota({ used: 0, limit: 0, paid_balance: 0 });
      setQuotaLoadError(false);
      return;
    }
    void refreshQuota();
  }, [authSession]);"""

if old_effect in s:
    s = s.replace(old_effect, new_effect)

# Replace the button + span for OCR display
old_block = """        <button
          type="button"
          className="rounded-md bg-white/85 px-2 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200 transition hover:bg-slate-50"
          onClick={onQuotaTap}
        >
          外部 OCR 本月：{gpuQuota.used}/{gpuQuota.limit}
          {quotaLoadError ? <span className="text-amber-600">（未加载）</span> : null}
        </button>
        {typeof gpuQuota.paid_balance === "number" && (
          <span className="ml-1 text-[11px] text-slate-400">余额：{gpuQuota.paid_balance}次</span>
        )}"""

new_block = """        <button
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
        </button>"""

if old_block in s:
    s = s.replace(old_block, new_block)
else:
    print("WARN: ocr button block not found, manual check")

g.write_text(s, encoding="utf-8")
print("GpuQuotaWidget ok")
