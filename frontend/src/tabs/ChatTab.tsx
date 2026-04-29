import { FormEvent, useEffect, useMemo, useState } from "react";
import { FileText, MessageSquare, Send, Sparkles, Trash2, Upload } from "lucide-react";
import { withTenantHeaders, type DocumentItem } from "../hooks/useDocuments";
import { API_BASE } from "../config/apiBase";
import { useEmbeddingModePreference } from "../lib/embeddingMode";

const CHAT_STORAGE_KEY = "xm_chat_state_v1";
const CHAT_SESSION_KEY = "xm_chat_session_id_v1";

type ChatItem = {
  role: "user" | "assistant";
  content: string;
  sources?: Array<{ title: string; section_path: string }>;
  examAnalysis?: {
    question_count: number;
    structure_summary?: { lines?: string[] };
    questions?: Array<{
      number_path?: string;
      question_type?: string;
      text?: string;
      section_title?: string | null;
      ai_answer?: string;
      brief_reasoning?: string[];
      evidence?: Array<{ title?: string; section_path?: string }>;
      material_text?: string | null;
      level?: number;
    }>;
  };
};

type Props = {
  documents: DocumentItem[];
  onUploadExamByChunks: (
    file: File,
    discipline: string,
    onUploadProgress?: (percent: number) => void
  ) => Promise<any>;
};

function loadPersistedState(): { messages: ChatItem[] } | null {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

async function readErrorMessage(resp: Response): Promise<string> {
  const fallback = `Request failed (${resp.status})`;
  try {
    const data = await resp.json();
    if (typeof data?.detail === "string" && data.detail.trim()) return data.detail.trim();
    if (typeof data?.message === "string" && data.message.trim()) return data.message.trim();
  } catch {
    // ignore
  }
  try {
    const text = (await resp.text()).trim();
    if (text) return text;
  } catch {
    // ignore
  }
  return fallback;
}

function ChatTab({ documents, onUploadExamByChunks }: Props) {
  const persisted = loadPersistedState();
  const [messages, setMessages] = useState<ChatItem[]>(persisted?.messages || []);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [queryStatus, setQueryStatus] = useState("");
  const [examFile, setExamFile] = useState<File | null>(null);
  const [lastExamFile, setLastExamFile] = useState<File | null>(null);
  const [examUploading, setExamUploading] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [embeddingMode, setEmbeddingMode] = useEmbeddingModePreference();

  const sessionId = useMemo(() => {
    const existed = localStorage.getItem(CHAT_SESSION_KEY);
    if (existed) return existed;
    const next = `sess-${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(CHAT_SESSION_KEY, next);
    return next;
  }, []);

  useEffect(() => {
    if (!documents.length) {
      setSelectedDocId(null);
      return;
    }
    if (selectedDocId != null && !documents.some((doc) => doc.id === selectedDocId)) {
      setSelectedDocId(null);
    }
  }, [documents, selectedDocId]);

  useEffect(() => {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify({ messages }));
  }, [messages]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || loading) return;

    const userText = query.trim();
    setQuery("");
    setMessages((prev) => [...prev, { role: "user", content: userText }]);
    setLoading(true);
    setQueryStatus("Searching...");

    try {
      const requestChat = async (mode: "local" | "api") =>
        fetch(`${API_BASE}/chat`, {
          method: "POST",
          headers: withTenantHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            query: userText,
            session_id: sessionId,
            embedding_mode: mode,
            scope: selectedDocId ? "document" : "library",
            document_id: selectedDocId ?? undefined,
          }),
        });

      let response = await requestChat(embeddingMode);

      if (!response.ok) {
        const firstError = await readErrorMessage(response);
        const tokenInsufficient =
          response.status === 429 && /embedding-3 token.*不足|token.*不足/i.test(firstError);
        if (tokenInsufficient && embeddingMode !== "local") {
          setQueryStatus("API 额度不足，切到本地向量重试...");
          response = await requestChat("local");
        } else {
          throw new Error(firstError);
        }
      }

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const data = await response.json();
      const aiItem: ChatItem = {
        role: "assistant",
        content: data.answer || "",
        sources: data.sources || [],
      };
      setMessages((prev) => [...prev, aiItem]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "Error: " + (err instanceof Error ? err.message : "Request failed") },
      ]);
    } finally {
      setLoading(false);
      setQueryStatus("");
    }
  };

  const handleClearChat = () => {
    if (!window.confirm("确认清空全部对话记录吗？")) return;
    setMessages([]);
    localStorage.removeItem(CHAT_STORAGE_KEY);
  };

  const runExamUpload = async (file: File) => {
    setExamUploading(true);
    try {
      const res = await onUploadExamByChunks(file, "all");
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `Exam analyzed: ${res.analysis.question_count} questions found.`,
          examAnalysis: {
            question_count: res.analysis.question_count,
            structure_summary: res.analysis.structure_summary,
            questions: Array.isArray(res.analysis.questions) ? res.analysis.questions : [],
          },
        },
      ]);
      setLastExamFile(file);
      setExamFile(null);
    } catch {
      alert("Exam analysis failed");
    } finally {
      setExamUploading(false);
    }
  };

  const renderExamAnalysis = (analysis?: ChatItem["examAnalysis"]) => {
    if (!analysis) return null;
    const summaryLines = Array.isArray(analysis.structure_summary?.lines)
      ? analysis.structure_summary.lines.filter(Boolean).slice(0, 3)
      : [];
    const questions = Array.isArray(analysis.questions) ? analysis.questions : [];

    return (
      <div className="mt-4 border-t-2 border-black/10 pt-3 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <span className="neo-box-sm bg-cyan-300 px-2 py-1 text-[10px] font-black uppercase">
            Parsed {analysis.question_count} Questions
          </span>
          <span className="text-[10px] font-black uppercase opacity-50">Exam Tree</span>
        </div>

        {summaryLines.length > 0 && (
          <div className="space-y-1">
            {summaryLines.map((line, idx) => (
              <div key={idx} className="text-[11px] font-bold leading-relaxed opacity-80">
                {line}
              </div>
            ))}
          </div>
        )}

        {questions.length > 0 && (
          <details className="neo-box-sm bg-slate-50 p-3" open>
            <summary className="cursor-pointer text-[11px] font-black uppercase tracking-wider">
              View Parsed Questions
            </summary>
            <div className="mt-3 space-y-2">
              {questions.map((question, idx) => (
                <details
                  key={`${question.number_path || idx}-${idx}`}
                  className="border-2 border-black/10 bg-white px-3 py-2"
                >
                  <summary className="cursor-pointer list-none">
                    <div className="flex flex-wrap items-center gap-2 text-[10px] font-black uppercase">
                      <span className="bg-yellow-300 px-1.5 py-0.5">Q{question.number_path || idx + 1}</span>
                      <span className="bg-pink-200 px-1.5 py-0.5">{question.question_type || "standard"}</span>
                      {question.section_title ? (
                        <span className="bg-cyan-100 px-1.5 py-0.5 normal-case">Section: {question.section_title}</span>
                      ) : null}
                      {typeof question.level === "number" ? (
                        <span className="bg-slate-200 px-1.5 py-0.5">L{question.level}</span>
                      ) : null}
                    </div>
                    <div className="mt-2 text-[12px] font-bold leading-relaxed whitespace-pre-wrap">
                      {question.text || "(empty question)"}
                    </div>
                  </summary>

                  <div className="mt-3 border-t-2 border-black/10 pt-3 space-y-3">
                    <div>
                      <div className="text-[10px] font-black uppercase opacity-50">AI Answer</div>
                      <div className="mt-1 text-[12px] font-bold leading-relaxed whitespace-pre-wrap">
                        {question.ai_answer || "暂无作答结果"}
                      </div>
                    </div>

                    {Array.isArray(question.brief_reasoning) && question.brief_reasoning.length > 0 && (
                      <div>
                        <div className="text-[10px] font-black uppercase opacity-50">Brief Reasoning</div>
                        <div className="mt-1 space-y-1">
                          {question.brief_reasoning.slice(0, 3).map((line, reasoningIdx) => (
                            <div
                              key={`${question.number_path || idx}-reason-${reasoningIdx}`}
                              className="text-[11px] font-bold leading-relaxed"
                            >
                              {reasoningIdx + 1}. {line}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {Array.isArray(question.evidence) && question.evidence.length > 0 && (
                      <div>
                        <div className="text-[10px] font-black uppercase opacity-50">Evidence</div>
                        <div className="mt-1 flex flex-wrap gap-2">
                          {question.evidence.slice(0, 4).map((item, evidenceIdx) => (
                            <div
                              key={`${question.number_path || idx}-evidence-${evidenceIdx}`}
                              className="neo-box-sm bg-yellow-100 px-2 py-1 text-[10px] font-black"
                            >
                              {(item.title || "Untitled").trim()}
                              {item.section_path ? ` (${item.section_path})` : ""}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {question.material_text ? (
                      <details className="neo-box-sm bg-cyan-50 p-2">
                        <summary className="cursor-pointer text-[10px] font-black uppercase tracking-wider">
                          View Related Material
                        </summary>
                        <div className="mt-2 text-[11px] font-bold leading-relaxed whitespace-pre-wrap">
                          {question.material_text}
                        </div>
                      </details>
                    ) : null}
                  </div>
                </details>
              ))}
            </div>
          </details>
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col h-[650px] gap-6">
      <div className="flex-1 overflow-y-auto pr-2 custom-scrollbar space-y-6">
        {messages.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-center opacity-30 grayscale p-10">
            <MessageSquare size={80} strokeWidth={1} />
            <p className="mt-4 text-xl font-black uppercase tracking-widest">暂无对话</p>
          </div>
        )}

        {messages.map((msg, idx) => (
          <div key={idx} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[85%] neo-box p-4 ${
                msg.role === "user" ? "bg-blue-400 text-white rotate-1" : "bg-white rotate-[-0.5deg]"
              }`}
            >
              <div className="text-[10px] font-black uppercase opacity-60 mb-2 border-b-2 border-current pb-1">
                {msg.role === "user" ? "用户" : "AI助手"}
              </div>
              <p className="text-sm font-bold leading-relaxed whitespace-pre-wrap">{msg.content}</p>

              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {msg.sources.map((s, i) => (
                    <div key={i} className="neo-box-sm bg-yellow-400 text-[9px] font-black px-2 py-1 flex items-center gap-1">
                      <FileText size={10} /> {s.title}
                    </div>
                  ))}
                </div>
              )}

              {renderExamAnalysis(msg.examAnalysis)}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="neo-box bg-yellow-400 p-4 animate-pulse">
              <p className="text-xs font-black uppercase">{queryStatus || "Thinking..."}</p>
            </div>
          </div>
        )}
      </div>

      <div className="neo-box bg-slate-900 p-6">
        <div className="flex justify-between items-center mb-4 gap-4">
          <div className="flex items-center gap-4 flex-wrap">
            <button
              onClick={handleClearChat}
              className="text-white hover:text-pink-500 transition-colors"
              title="Clear Chat"
            >
              <Trash2 size={20} />
            </button>
            <div className="h-6 w-1 bg-white/20" />
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-black uppercase text-white/40 tracking-widest">范围:</span>
              <select
                className="bg-transparent text-white font-black uppercase text-[10px] outline-none cursor-pointer hover:text-blue-400"
                value={selectedDocId ?? ""}
                onChange={(e) => setSelectedDocId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="" className="bg-slate-900">
                  Knowledge Base
                </option>
                {documents.map((doc) => (
                  <option key={doc.id} value={doc.id} className="bg-slate-900">
                    {doc.filename || doc.title}
                  </option>
                ))}
              </select>
              {selectedDocId ? (
                <span className="text-[9px] text-green-400 font-bold ml-2 bg-green-400/10 px-1.5 py-0.5 border border-green-400/20">
                  仅当前文档
                </span>
              ) : (
                <span className="text-[9px] text-yellow-400 font-bold ml-2 bg-yellow-400/10 px-1.5 py-0.5 border border-yellow-400/20">
                  全部文档
                </span>
              )}
            </div>
            <div className="h-6 w-1 bg-white/20" />
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-black uppercase text-white/40 tracking-widest">向量:</span>
              <button
                type="button"
                onClick={() => setEmbeddingMode("local")}
                className={`px-2 py-1 text-[10px] font-black uppercase border ${
                  embeddingMode === "local"
                    ? "bg-cyan-300 text-slate-900 border-cyan-300"
                    : "text-white/70 border-white/20"
                }`}
              >
                本地
              </button>
              <button
                type="button"
                onClick={() => setEmbeddingMode("api")}
                className={`px-2 py-1 text-[10px] font-black uppercase border ${
                  embeddingMode === "api"
                    ? "bg-pink-400 text-white border-pink-400"
                    : "text-white/70 border-white/20"
                }`}
              >
                API
              </button>
            </div>
          </div>

          {lastExamFile ? (
            <button
              type="button"
              onClick={() => runExamUpload(lastExamFile)}
              disabled={examUploading}
              className="text-[10px] font-black uppercase tracking-widest text-cyan-300 hover:text-cyan-100 disabled:opacity-40"
            >
              重试上次试卷
            </button>
          ) : null}

          <label className="cursor-pointer group">
            <input
              type="file"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0] || null;
                setExamFile(file);
                if (file) setLastExamFile(file);
              }}
            />
            <div className="flex items-center gap-2 text-white/60 group-hover:text-pink-400 transition-colors">
              <Upload size={16} />
              <span className="text-[10px] font-black uppercase tracking-widest">上传考试</span>
            </div>
          </label>
        </div>

        {examFile && (
          <div className="neo-box-sm bg-pink-500 text-white p-2 mb-4 flex justify-between items-center">
            <span className="text-xs font-black truncate max-w-[200px]">{examFile.name}</span>
            <button
              onClick={() => runExamUpload(examFile)}
              disabled={examUploading}
              className="bg-white text-slate-900 px-3 py-1 text-[10px] font-black uppercase"
            >
              {examUploading ? "分析中..." : "开始分析"}
            </button>
          </div>
        )}

        <form onSubmit={handleSubmit} className="relative flex gap-3">
          <textarea
            className="flex-1 neo-input min-h-[50px] max-h-[150px] py-2 pr-12 text-sm font-bold placeholder:opacity-50"
            placeholder="询问关于文档的任何内容..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
          />
          <button
            type="submit"
            disabled={!query.trim() || loading}
            className="neo-button bg-pink-500 text-white p-4 disabled:opacity-30 disabled:grayscale transition-all"
          >
            <Send size={20} />
          </button>
        </form>

        <div className="mt-4 flex justify-center">
          <div className="flex items-center gap-2 text-[9px] font-black text-white/30 uppercase tracking-[0.3em]">
            <Sparkles size={10} />
            AI Knowledge Engine
          </div>
        </div>
      </div>
    </div>
  );
}

export default ChatTab;
