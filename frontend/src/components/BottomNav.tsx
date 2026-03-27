import { AppTab } from "../App";

type Props = {
  activeTab: AppTab;
  onChange: (tab: AppTab) => void;
};

const items: { key: AppTab; label: string }[] = [
  { key: "upload", label: "上传" },
  { key: "knowledge", label: "要点总结" },
  { key: "chat", label: "查询" },
];

function BottomNav({ activeTab, onChange }: Props) {
  return (
    <nav className="fixed bottom-0 left-0 right-0 z-20 border-t border-white/70 bg-white/72 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center gap-2 px-2 py-2">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            className={`flex-1 rounded-2xl py-2.5 text-sm font-semibold transition-all duration-200 ${
              activeTab === item.key
                ? "bg-gradient-to-r from-pink-500 to-violet-500 text-white shadow-[0_10px_22px_-14px_rgba(124,92,255,0.9)]"
                : "text-slate-500 hover:bg-white/85 hover:text-violet-500"
            }`}
            onClick={() => onChange(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

export default BottomNav;
