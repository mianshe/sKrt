export type SourceItem = {
  title: string;
  discipline: string;
  section_path: string;
  document_type: string;
};

export type ChatItem = {
  role: "user" | "assistant";
  content: string;
  brief_reasoning?: string[];
  agent_trace?: string[];
  sources?: SourceItem[];
  cross_discipline?: { discipline: string; title: string; reason: string }[];
};

type Props = {
  item: ChatItem;
};

function ChatMessage({ item }: Props) {
  const isUser = item.role === "user";
  return (
    <div className={`mb-3 flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[92%] rounded-3xl px-3 py-2 text-sm shadow-sm ${
          isUser
            ? "bg-gradient-to-r from-pink-500 to-violet-500 text-white shadow-[0_10px_20px_-12px_rgba(124,92,255,0.9)]"
            : "bg-white/92 text-slate-800 ring-1 ring-violet-100"
        }`}
      >
        <p className="whitespace-pre-wrap leading-6">{item.content}</p>

        {!isUser && (item.brief_reasoning?.length ?? 0) > 0 && (
          <details className="mt-2 rounded-xl border border-violet-100 bg-violet-50/70 px-2 py-1 text-xs text-slate-700">
            <summary className="cursor-pointer select-none font-medium text-slate-700">简版思路（最多3条）</summary>
            <ul className="mt-1 list-disc pl-4">
              {item.brief_reasoning?.slice(0, 3).map((line, idx) => (
                <li key={`brief-${idx}`}>{line}</li>
              ))}
            </ul>
          </details>
        )}

        {!isUser && (item.agent_trace?.length ?? 0) > 0 && (
          <details className="mt-2 rounded-xl border border-pink-100 bg-pink-50/70 px-2 py-1 text-xs text-fuchsia-700">
            <summary className="cursor-pointer select-none font-medium">Agent 执行痕迹</summary>
            <div className="mt-1 flex flex-wrap gap-1">
              {item.agent_trace?.map((step, idx) => (
                <span key={`trace-${idx}`} className="rounded-full bg-white px-2 py-0.5 ring-1 ring-pink-200">
                  {step}
                </span>
              ))}
            </div>
          </details>
        )}

        {!isUser && (item.cross_discipline?.length ?? 0) > 0 && (
          <div className="mt-2 rounded-xl border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-900">
            检测到跨学科关联：{item.cross_discipline?.map((c) => c.discipline).join(" / ")}
          </div>
        )}

        {!isUser && (item.sources?.length ?? 0) > 0 && (
          <div className="mt-2 grid gap-1">
            {item.sources?.slice(0, 3).map((s, idx) => (
              <div key={`${s.title}-${idx}`} className="rounded-xl bg-violet-50/65 px-2 py-1 text-xs text-slate-600 ring-1 ring-violet-100">
                来源：{s.title} · {s.section_path} · {s.discipline}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default ChatMessage;
