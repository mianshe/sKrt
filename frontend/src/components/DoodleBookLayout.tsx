import React from "react";
import { BookOpenText, MessageSquare, Upload, type LucideIcon } from "lucide-react";

type TabId = "upload" | "knowledge" | "chat";

type ShelfDocument = {
  id: number;
  title: string;
  createdAt?: string;
};

interface DopamineLayoutProps {
  currentTab: TabId;
  onTabChange: (tab: TabId) => void;
  documents?: ShelfDocument[];
  children: React.ReactNode;
  authElement?: React.ReactNode;
}

const NAV_ITEMS: Array<{ id: TabId; icon: LucideIcon; label: string }> = [
  { id: "upload", icon: Upload, label: "上传区" },
  { id: "knowledge", icon: BookOpenText, label: "解析区" },
  { id: "chat", icon: MessageSquare, label: "问答区" },
];

const DopamineLayout: React.FC<DopamineLayoutProps> = ({
  currentTab,
  onTabChange,
  documents = [],
  children,
  authElement,
}) => {
  return (
    <div className="min-h-screen bg-slate-50 p-4 font-bold text-slate-900 md:p-8">
      <header className="mx-auto mb-8 max-w-6xl">
        <div className="mb-8 flex flex-col items-center justify-between gap-6 lg:flex-row">
          <div className="neo-box -rotate-1 bg-yellow-400 px-6 py-3">
            <h1 className="text-3xl font-black uppercase tracking-tighter md:text-4xl">
              何芯面试用测试项目demo
            </h1>
          </div>

          <div className="flex flex-wrap items-center justify-center gap-4">
            <nav className="flex gap-2">
              {NAV_ITEMS.map((item) => (
                <button
                  key={item.id}
                  onClick={() => onTabChange(item.id)}
                  className={`neo-button-sm flex items-center gap-2 ${
                    currentTab === item.id
                      ? "translate-x-0.5 translate-y-0.5 bg-pink-400 text-white shadow-none"
                      : "bg-white hover:bg-slate-100"
                  }`}
                >
                  <item.icon size={18} />
                  <span>{item.label}</span>
                </button>
              ))}
            </nav>
            {authElement && <div className="lg:ml-4">{authElement}</div>}
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl grid-cols-1 gap-8 lg:grid-cols-4">
        <div className={`space-y-4 lg:col-span-1 ${currentTab !== "upload" ? "hidden lg:block" : ""}`}>
          <div className="neo-box h-full max-h-[70vh] rotate-1 bg-blue-400 p-4">
            <h2 className="mb-4 border-b-4 border-slate-900 pb-2 text-xl font-black uppercase">文档列表</h2>
            <div className="custom-scrollbar flex-1 space-y-3 overflow-y-auto pr-2">
              {documents.length === 0 ? (
                <p className="py-4 text-sm italic opacity-80">暂无文档。</p>
              ) : (
                documents.map((doc) => (
                  <div key={doc.id} className="neo-box-sm group cursor-pointer bg-white p-3 hover:bg-yellow-50">
                    <p className="truncate text-xs font-black group-hover:text-blue-600">{doc.title}</p>
                    <p className="mt-1 text-[10px] uppercase opacity-60">{doc.createdAt}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        <div className="lg:col-span-3">
          <div
            className={`neo-box min-h-[70vh] p-6 md:p-10 ${
              currentTab === "upload" ? "bg-white" : currentTab === "knowledge" ? "bg-slate-50" : "bg-white"
            }`}
          >
            <div className="neo-box-sm -ml-12 mb-10 inline-block rotate-[-2deg] bg-slate-900 px-4 py-1 text-white uppercase">
              {NAV_ITEMS.find((item) => item.id === currentTab)?.label}
            </div>

            <div className="relative">{children}</div>
          </div>
        </div>
      </main>

      <footer className="mx-auto mt-16 flex max-w-6xl flex-col items-center gap-4 border-t-4 border-slate-900 pt-8 opacity-40">
        <div className="text-xs font-black uppercase tracking-widest">AI助手 · 知识引擎 · 2026</div>
      </footer>
    </div>
  );
};

export default DopamineLayout;
