import { useEffect, useRef, useState } from "react";
import AuthPanel from "./components/AuthPanel";
import GpuQuotaWidget from "./components/GpuQuotaWidget";
import NoticeDrawer from "./components/NoticeDrawer";
import { API_BASE } from "./config/apiBase";
import { ensureAuthReady, setAccessToken, useAccessToken, useAuthBootstrapStatus, verifyLocalAuthSession } from "./lib/auth";
import ChatTab from "./tabs/ChatTab";
import KnowledgeTab from "./tabs/KnowledgeTab";
import UploadTab from "./tabs/UploadTab";
import { useDocuments } from "./hooks/useDocuments";
import DoodleBookLayout from "./components/DoodleBookLayout";
import { Bell } from "lucide-react";

export type AppTab = "upload" | "knowledge" | "chat";

function App() {
  const [tab, setTab] = useState<AppTab>("upload");
  const { documents, loading, error, refreshDocuments, createUploadTasks, getUploadTask, uploadExamByChunks, deleteDocument } =
    useDocuments();

  const [noticeOpen, setNoticeOpen] = useState(false);
  const [authLocalEnabled, setAuthLocalEnabled] = useState(true);
  const [authSession, setAuthSession] = useState(0);
  const knowledgeRefreshKey = `${authSession}:${documents
    .map((doc) => String(doc.id))
    .sort()
    .join(",")}`;
  const accessToken = useAccessToken();
  const authBootstrapStatus = useAuthBootstrapStatus();
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
    void ensureAuthReady();
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
      if (authBootstrapStatus !== "ready") {
        return;
      }
      if (!accessToken) {
        if (!isInitialSync) refreshAfterAuthChange();
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
    return () => { cancelled = true; };
  }, [accessToken, authBootstrapStatus, refreshDocuments]);

  return (
    <DoodleBookLayout
      currentTab={tab}
      onTabChange={setTab}
      documents={documents.map((doc) => ({
        id: doc.id,
        title: doc.filename || doc.title || `文档 ${doc.id}`,
        createdAt: new Date(doc.created_at).toLocaleDateString(),
      }))}
      authElement={
        <div className="flex items-center gap-3">
          <AuthPanel onAuthed={refreshDocuments} />
          <button 
            onClick={() => setNoticeOpen(true)}
            className="neo-button-sm bg-yellow-400 p-3 hover:bg-pink-400 hover:text-white group relative"
          >
            <Bell size={20} />
            <span className="absolute -top-1 -right-1 w-3 h-3 bg-red-500 rounded-full border-2 border-slate-900 group-hover:scale-125 transition-transform" />
          </button>
        </div>
      }
    >
      {!accessToken && authLocalEnabled && (
        <div className="mb-6 neo-box-sm bg-cyan-300 p-4 text-xs font-black uppercase rotate-[-1deg]">
          当前为演示资料库。登录后将自动切换到你的个人资料库。
        </div>
      )}

      <section hidden={tab !== "upload"} aria-hidden={tab !== "upload"}>
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
          authReady={authBootstrapStatus === "ready"}
        />
      </section>

      <section hidden={tab !== "knowledge"} aria-hidden={tab !== "knowledge"}>
        <KnowledgeTab refreshKey={knowledgeRefreshKey} documents={documents} />
      </section>

      <section hidden={tab !== "chat"} aria-hidden={tab !== "chat"}>
        <ChatTab documents={documents} onUploadExamByChunks={uploadExamByChunks} />
      </section>

      {/* 容量告警提示 */}
      {capacityWarn && (
        <div className={`fixed bottom-8 left-8 z-50 neo-box-sm font-black text-xs ${
          capacityWarn === "hard" ? "bg-red-400 text-white animate-bounce" : "bg-amber-400"
        } p-4 rotate-[-2deg]`}>
          <div className="flex items-center gap-2">
            <span className="text-xl">⚠️</span>
                            <div>
                              <p className="font-black">系统存储空间{capacityWarn === "hard" ? "已满" : "不足"}</p>
                              <p className="opacity-80 text-[10px]">请管理您的文档</p>
                            </div>          </div>
        </div>
      )}

      <NoticeDrawer open={noticeOpen} onClose={() => setNoticeOpen(false)} />
      
      {/* GPU Quota Widget */}
      <div className="fixed bottom-8 left-8 z-40 hidden lg:block hover:scale-105 transition-transform">
        <GpuQuotaWidget authReady={authBootstrapStatus === "ready"} />
      </div>
    </DoodleBookLayout>
  );
}

// Fixed property reference in tab mapping
const getGetUploadTask = (id: any) => Promise.resolve({} as any); 

export default App;
