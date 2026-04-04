import { useEffect, useRef, useState } from "react";
import AuthPanel from "./components/AuthPanel";
import BottomNav from "./components/BottomNav";
import GpuQuotaWidget from "./components/GpuQuotaWidget";
import NoticeDrawer from "./components/NoticeDrawer";
import { API_BASE } from "./config/apiBase";
import { setAccessToken, useAccessToken, verifyLocalAuthSession } from "./lib/auth";
import ChatTab from "./tabs/ChatTab";
import KnowledgeTab from "./tabs/KnowledgeTab";
import UploadTab from "./tabs/UploadTab";
import { useDocuments } from "./hooks/useDocuments";

export type AppTab = "upload" | "knowledge" | "chat";

function App() {
  const [tab, setTab] = useState<AppTab>("upload");
  const { documents, loading, error, refreshDocuments, createUploadTasks, getUploadTask, uploadExamByChunks, deleteDocument } =
    useDocuments();

  const [noticeOpen, setNoticeOpen] = useState(false);
  const [mobileToolsOpen, setMobileToolsOpen] = useState(false);
  const [authLocalEnabled, setAuthLocalEnabled] = useState(true);
  const [authSession, setAuthSession] = useState(0);
  const knowledgeRefreshKey = `${authSession}:${documents
    .map((doc) => String(doc.id))
    .sort()
    .join(",")}`;
  const accessToken = useAccessToken();
  const didInitAuthSyncRef = useRef(false);

  const [capacityWarn, setCapacityWarn] = useState<"soft" | "hard" | null>(null);

  useEffect(() => {
    const check = async () => {
      try {
        const response = await fetch(`${API_BASE}/health`);
        if (!response.ok) return;
        const data = await response.json();

        if (typeof data?.auth_local_jwt_enabled === "boolean") {
          setAuthLocalEnabled(data.auth_local_jwt_enabled);
        }

        const cap = data?.capacity;
        if (cap?.hard_exceeded) setCapacityWarn("hard");
        else if (cap?.soft_exceeded) setCapacityWarn("soft");
        else setCapacityWarn(null);
      } catch {
        // ignore network errors
      }
    };

    void check();
    const id = setInterval(check, 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const isInitialSync = !didInitAuthSyncRef.current;
    didInitAuthSyncRef.current = true;

    const refreshAfterAuthChange = () => {
      if (cancelled) return;
      setAuthSession((n) => n + 1);
      void refreshDocuments();
    };

    const syncAuthState = async () => {
      if (!accessToken) {
        if (!isInitialSync) {
          refreshAfterAuthChange();
        }
        return;
      }

      if (isInitialSync) {
        const verified = await verifyLocalAuthSession(accessToken);
        if (cancelled) return;
        if (!verified) {
          setAccessToken(null);
          return;
        }
      }

      refreshAfterAuthChange();
    };

    void syncAuthState();
    return () => {
      cancelled = true;
    };
  }, [accessToken, refreshDocuments]);

  return (
    <div className="min-h-screen text-slate-900">
      <header className="sticky top-0 z-10 border-b border-white/60 bg-white/70 px-4 py-3.5 backdrop-blur">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h1 className="bg-gradient-to-r from-pink-500 via-violet-500 to-teal-500 bg-clip-text py-0.5 text-2xl font-black tracking-wide text-transparent drop-shadow-[0_6px_18px_rgba(124,92,255,0.25)] md:text-3xl">
              资料解析
            </h1>
            <p className="mt-0.5 text-sm font-medium text-slate-500">何芯求职专用测试项目</p>
          </div>

          <div className="mt-0.5 md:hidden">
            <button
              type="button"
              className="rounded-xl bg-white/85 px-3 py-2 text-sm font-semibold text-slate-700 ring-1 ring-slate-200 transition hover:bg-white"
              onClick={() => setMobileToolsOpen(true)}
            >
              菜单
            </button>
          </div>

          <div className="mt-0.5 hidden max-w-[380px] flex-col items-end gap-2 md:flex">
            {authLocalEnabled && (
              <AuthPanel
                onAuthed={() => {
                  setMobileToolsOpen(false);
                }}
              />
            )}
            <GpuQuotaWidget authSession={authSession} />
          </div>
        </div>
      </header>

      {mobileToolsOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 px-4 pt-16" onClick={() => setMobileToolsOpen(false)}>
          <div
            className="w-full max-w-md rounded-2xl bg-white p-4 shadow-xl ring-1 ring-slate-200"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between gap-2">
              <p className="text-sm font-semibold text-slate-800">快捷菜单</p>
              <button
                type="button"
                className="rounded-lg px-2 py-1 text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                onClick={() => setMobileToolsOpen(false)}
                aria-label="关闭"
              >
                ×
              </button>
            </div>

            <div className="mt-3 space-y-3">
              {authLocalEnabled && (
                <AuthPanel
                  onAuthed={() => {
                    setMobileToolsOpen(false);
                  }}
                />
              )}
              <GpuQuotaWidget authSession={authSession} />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm font-semibold text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={() => {
                    setNoticeOpen(true);
                    setMobileToolsOpen(false);
                  }}
                >
                  公告
                </button>
                <button
                  type="button"
                  className="rounded-2xl bg-white/85 px-3 py-2 text-sm font-semibold text-slate-700 ring-1 ring-slate-200 transition hover:bg-slate-50"
                  onClick={() => setMobileToolsOpen(false)}
                >
                  关闭
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {capacityWarn === "hard" && (
        <div className="sticky top-[57px] z-10 bg-red-600 px-4 py-2 text-center text-sm font-semibold text-white shadow">
          服务器存储空间不足，已暂停写入。请清理或扩容后继续使用。
        </div>
      )}

      {capacityWarn === "soft" && (
        <div className="sticky top-[57px] z-10 bg-amber-500 px-4 py-2 text-center text-sm font-semibold text-white shadow">
          服务器存储空间即将用尽，请尽快清理不需要的文档。
        </div>
      )}

      <NoticeDrawer open={noticeOpen} onClose={() => setNoticeOpen(false)} widthPx={360} />

      <button
        type="button"
        className="fixed right-3 top-28 z-30 hidden rounded-2xl bg-white/80 px-3 py-2 text-sm font-semibold text-slate-700 shadow-lg ring-1 ring-slate-200 transition hover:bg-white md:block"
        onClick={() => setNoticeOpen(true)}
      >
        公告
      </button>

      <main className={`mx-auto w-full max-w-5xl px-3 pb-24 pt-4 transition-[padding] duration-200 ${noticeOpen ? "md:pr-[360px]" : ""}`}>
        <div className={tab === "upload" ? "block" : "hidden"}>
          <UploadTab
            documents={documents}
            loading={loading}
            error={error}
            onCreateUploadTasks={createUploadTasks}
            onGetTask={getUploadTask}
            onDelete={deleteDocument}
            onRefresh={refreshDocuments}
            authLocalEnabled={authLocalEnabled}
            authSession={authSession}
          />
        </div>

        <div className={tab === "knowledge" ? "block" : "hidden"}>
          <KnowledgeTab refreshKey={knowledgeRefreshKey} />
        </div>

        <div className={tab === "chat" ? "block" : "hidden"}>
          <ChatTab onUploadExamByChunks={uploadExamByChunks} />
        </div>
      </main>

      <BottomNav activeTab={tab} onChange={setTab} />
    </div>
  );
}

export default App;
