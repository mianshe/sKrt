import { FormEvent, useEffect, useMemo, useState } from "react";
import ChatMessage, { ChatItem } from "../components/ChatMessage";
import { ExamChunkUploadResult } from "../hooks/useDocuments";

const API_BASE = (globalThis as any).__API_BASE__ || "http://localhost:8000";
const CHAT_STORAGE_KEY = "xm_chat_state_v1";
const CHAT_SESSION_KEY = "xm_chat_session_id_v1";
const TENANT_KEY = "xm_tenant_id";

type Props = {
  onUploadExamByChunks: (file: File, discipline: string, onUploadProgress?: (percent: number) => void) => Promise<ExamChunkUploadResult>;
};

type ExamAnalysis = {
  question_count: number;
  difficulty: {
    average_score: number;
    distribution: { easy: number; medium: number; hard: number };
  };
  questions: Array<{
    id: number;
    text: string;
    level?: number;
    number_path?: string;
    marker_type?: string;
    question_type?: string;
    difficulty_score: number;
    difficulty_level: string;
    ai_answer?: string;
    brief_reasoning?: string[];
    evidence?: Array<{ title: string; section_path: string; discipline: string }>;
    answer_strategy?: {
      concept_induction: string;
      information_compression: string;
      reverse_check: string;
      distractor_design: string;
    };
    qa_gates?: {
      consistency: boolean;
      evidence_traceable: boolean;
      reasoning_visibility: boolean;
      passed: boolean;
      failed_checks: string[];
    };
    options?: Array<{ label: string; text: string }>;
    material_id?: string | null;
    material_text?: string | null;
    parent_path?: string | null;
    section_title?: string | null;
  }>;
  qa_regression_gates?: {
    consistency_pass_rate: number;
    evidence_traceable_pass_rate: number;
    reasoning_visibility_pass_rate: number;
    overall_pass_rate: number;
  };
  recommendations: Array<{ rank: number; title: string; section_path: string; reason: string }>;
};

const QUESTION_TYPE_LABELS: Record<string, string> = {
  choice: "选择题", fill_blank: "填空题", true_false: "判断题",
  short_answer: "简答题", essay: "论述题", calculation: "计算题",
  proof: "证明题", design: "设计题", material_analysis: "材料分析题",
  standard: "标准题",
};

type PersistedChatState = {
  messages: ChatItem[];
  query: string;
  latestExamAnalysis: ExamAnalysis | null;
};

function loadPersistedState(): PersistedChatState | null {
  try {
    const raw = localStorage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as PersistedChatState;
  } catch {
    return null;
  }
}

function ChatTab({ onUploadExamByChunks }: Props) {
  const persisted = loadPersistedState();
  const [messages, setMessages] = useState<ChatItem[]>(persisted?.messages || []);
  const [query, setQuery] = useState(persisted?.query || "");
  const discipline = "all";
  const mode: "free" = "free";
  const [loading, setLoading] = useState(false);
  const [examFile, setExamFile] = useState<File | null>(null);
  const [examUploading, setExamUploading] = useState(false);
  const [examUploadProgress, setExamUploadProgress] = useState(0);
  const [examError, setExamError] = useState("");
  const [latestExamAnalysis, setLatestExamAnalysis] = useState<ExamAnalysis | null>(persisted?.latestExamAnalysis || null);
  const sessionId = useMemo(() => {
    const existed = localStorage.getItem(CHAT_SESSION_KEY);
    if (existed && existed.trim()) return existed.trim();
    const next = `sess-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`;
    localStorage.setItem(CHAT_SESSION_KEY, next);
    return next;
  }, []);
  const tenantId = useMemo(() => localStorage.getItem(TENANT_KEY)?.trim() || "public", []);

  const disabled = useMemo(() => !query.trim() || loading, [query, loading]);
  useEffect(() => {
    const payload: PersistedChatState = {
      messages,
      query,
      latestExamAnalysis,
    };
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(payload));
  }, [messages, query, latestExamAnalysis]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (disabled) return;

    const userText = query.trim();
    setQuery("");
    setMessages((prev) => [...prev, { role: "user", content: userText }]);
    setLoading(true);

    try {
      const resp = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Tenant-Id": tenantId },
        body: JSON.stringify({ query: userText, discipline, mode, session_id: sessionId }),
      });
      if (!resp.ok) throw new Error("查询请求失败");
      const data = await resp.json();

      const fullText = String(data.answer || "");
      const aiItem: ChatItem = {
        role: "assistant",
        content: "",
        brief_reasoning: Array.isArray(data.brief_reasoning) ? data.brief_reasoning : [],
        agent_trace: Array.isArray(data.agent_trace) ? data.agent_trace : [],
        sources: data.sources || [],
        cross_discipline: data.cross_discipline || [],
      };
      setMessages((prev) => [...prev, aiItem]);

      for (let i = 1; i <= fullText.length; i += 5) {
        await new Promise((r) => setTimeout(r, 16));
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          if (last?.role === "assistant") {
            next[next.length - 1] = { ...last, content: fullText.slice(0, i) };
          }
          return next;
        });
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: err instanceof Error ? err.message : "请求失败，请稍后重试。" },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleExamUpload = async () => {
    if (!examFile) return;
    setExamUploading(true);
    setExamError("");
    setExamUploadProgress(0);
    try {
      const payload = await onUploadExamByChunks(examFile, discipline || "all", setExamUploadProgress);
      const analysis = (payload.analysis || null) as ExamAnalysis | null;
      if (!analysis) throw new Error("未返回有效分析结果");
      setLatestExamAnalysis(analysis);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `已完成题目文件解析与作答：共 ${analysis.question_count} 题，平均难度 ${analysis.difficulty?.average_score ?? 0}。`,
        },
      ]);
      setExamFile(null);
    } catch (e) {
      setExamError(e instanceof Error ? e.message : "题目上传分析失败");
    } finally {
      setExamUploading(false);
    }
  };

  const handleClearChat = async () => {
    if (!window.confirm("确认清空当前对话与本地缓存吗？")) return;
    try {
      await fetch(`${API_BASE}/chat/memory`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json", "X-Tenant-Id": tenantId },
        body: JSON.stringify({ session_id: sessionId }),
      });
    } catch {
      // ignore network errors for local clear
    }
    setMessages([]);
    setQuery("");
    setLatestExamAnalysis(null);
    setExamError("");
    setExamFile(null);
    setExamUploadProgress(0);
    localStorage.removeItem(CHAT_STORAGE_KEY);
  };

  return (
    <section className="space-y-3">
      <div className="card p-4">
        <p className="text-sm font-medium text-violet-600">当前模式：查询</p>
        <p className="mt-1 text-xs text-slate-500">学科与文档类型由系统自动理解。</p>
      </div>

      <div className="card h-[430px] overflow-y-auto p-4">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs text-slate-500">对话记录</p>
          <button className="btn-primary" type="button" onClick={handleClearChat} disabled={loading || examUploading}>
            清空对话
          </button>
        </div>
        {messages.length === 0 && <p className="text-sm text-slate-500">输入问题开始对话，系统将自动返回溯源片段。</p>}
        {messages.map((m, idx) => (
          <ChatMessage key={idx} item={m} />
        ))}
      </div>

      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-violet-600">✦ 上传题目文件自动解析并作答</h3>
        <input
          type="file"
          className="input"
          accept=".pdf,.doc,.docx,.txt,.md,.markdown"
          onChange={(e) => setExamFile(e.target.files?.[0] || null)}
        />
        <div className="mt-2 flex items-center justify-between">
          <p className="text-xs text-slate-500">
            {examFile ? `已选择：${examFile.name}` : "上传题目文件后会自动走解析 + 作答流程"}
          </p>
          <button className="btn-primary" type="button" disabled={!examFile || examUploading} onClick={handleExamUpload}>
            {examUploading ? "分析中..." : "上传并分析"}
          </button>
        </div>
        {examUploading && (
          <div className="mt-2">
            <div className="mb-1 flex items-center justify-between text-[11px] text-slate-600">
              <span>题目文件上传</span>
              <span>{examUploadProgress}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200">
              <div className="h-full bg-indigo-600 transition-all" style={{ width: `${examUploadProgress}%` }} />
            </div>
          </div>
        )}
        {examError && <p className="mt-2 text-xs text-rose-600">{examError}</p>}

        {latestExamAnalysis && (
          <div className="mt-3 space-y-2 rounded-2xl bg-gradient-to-br from-white to-violet-50/70 p-3 ring-1 ring-violet-100">
            <p className="text-xs text-slate-700">
              题目数：{latestExamAnalysis.question_count} · 平均难度：{latestExamAnalysis.difficulty?.average_score ?? 0}
            </p>
            <p className="text-xs text-slate-700">
              难度分布：易 {latestExamAnalysis.difficulty?.distribution?.easy ?? 0} / 中{" "}
              {latestExamAnalysis.difficulty?.distribution?.medium ?? 0} / 难 {latestExamAnalysis.difficulty?.distribution?.hard ?? 0}
            </p>
            <div>
              <p className="text-xs font-semibold text-slate-700">
                AI 作答（共 {latestExamAnalysis.questions?.length ?? latestExamAnalysis.question_count} 题）
              </p>
              <ul className="mt-1 max-h-[min(60vh,520px)] space-y-2 overflow-y-auto pr-1 text-xs text-slate-700">
                {(latestExamAnalysis.questions || []).map((q) => {
                  const preview =
                    q.text.length > 100 ? `${q.text.slice(0, 100).replace(/\s+/g, " ")}…` : q.text;
                  return (
                    <li key={`qa-${q.id}`} className="rounded-lg border border-slate-200 bg-white">
                      <details className="group px-2 py-2">
                        <summary className="cursor-pointer list-none font-medium marker:content-none [&::-webkit-details-marker]:hidden">
                          <span className="text-indigo-700">{q.number_path ? `Q${q.number_path}` : `Q${q.id}`}</span>
                          <span className="ml-1 text-slate-600">{preview}</span>
                        </summary>
                        <div className="mt-2 border-t border-slate-100 pt-2">
                          <p className="whitespace-pre-wrap text-slate-800">{q.text}</p>
                          <p className="mt-2 text-slate-600">{q.ai_answer || "暂无作答结果"}</p>
                          {Array.isArray(q.brief_reasoning) && q.brief_reasoning.length > 0 && (
                            <details className="mt-1 rounded-md bg-slate-50 px-2 py-1 text-[11px] text-slate-600">
                              <summary className="cursor-pointer font-medium">简版思路</summary>
                              <ul className="mt-1 list-disc pl-4">
                                {q.brief_reasoning.slice(0, 3).map((line, idx) => (
                                  <li key={`exam-brief-${q.id}-${idx}`}>{line}</li>
                                ))}
                              </ul>
                            </details>
                          )}
                          {Array.isArray(q.evidence) && q.evidence.length > 0 && (
                            <p className="mt-1 text-[11px] text-slate-500">
                              依据：{q.evidence.map((e) => `${e.title}(${e.section_path})`).join("；")}
                            </p>
                          )}
                          {q.answer_strategy && (
                            <p className="mt-1 text-[11px] text-slate-500">
                              干扰项设计：{q.answer_strategy.distractor_design || "暂无说明"}
                            </p>
                          )}
                          <p className="mt-1 text-[11px] text-slate-500">
                            层级：{q.level ?? 1} · 题型：{QUESTION_TYPE_LABELS[q.question_type || "standard"] || q.question_type}
                            {q.section_title && ` · 所属：${q.section_title}`}
                          </p>
                          {q.options && q.options.length > 0 && (
                            <div className="mt-1 space-y-0.5">
                              {q.options.map((opt) => (
                                <p key={opt.label} className="text-[11px] text-slate-600 pl-2">
                                  <span className="font-semibold">{opt.label}.</span> {opt.text}
                                </p>
                              ))}
                            </div>
                          )}
                          {q.material_text && (
                            <details className="mt-1">
                              <summary className="text-[11px] text-indigo-500 cursor-pointer">查看相关材料</summary>
                              <p className="mt-0.5 text-[11px] text-slate-500 whitespace-pre-wrap pl-2">
                                {q.material_text.slice(0, 600)}{q.material_text.length > 600 ? "..." : ""}
                              </p>
                            </details>
                          )}
                          {q.qa_gates && (
                            <p className={`mt-1 text-[11px] ${q.qa_gates.passed ? "text-emerald-600" : "text-amber-600"}`}>
                              门禁：一致性 {q.qa_gates.consistency ? "通过" : "未通过"} / 证据可追溯{" "}
                              {q.qa_gates.evidence_traceable ? "通过" : "未通过"} / 思路可见性{" "}
                              {q.qa_gates.reasoning_visibility ? "通过" : "未通过"}
                            </p>
                          )}
                        </div>
                      </details>
                    </li>
                  );
                })}
              </ul>
            </div>
            {latestExamAnalysis.qa_regression_gates && (
              <p className="text-[11px] text-slate-600">
                回归门禁通过率：一致性 {(latestExamAnalysis.qa_regression_gates.consistency_pass_rate * 100).toFixed(0)}% · 证据可追溯{" "}
                {(latestExamAnalysis.qa_regression_gates.evidence_traceable_pass_rate * 100).toFixed(0)}% · 思路可见性{" "}
                {(latestExamAnalysis.qa_regression_gates.reasoning_visibility_pass_rate * 100).toFixed(0)}%
              </p>
            )}
            <div>
              <p className="text-xs font-semibold text-slate-700">推荐资料</p>
              <ul className="mt-1 space-y-1 text-xs text-slate-600">
                {(latestExamAnalysis.recommendations || []).slice(0, 4).map((item) => (
                  <li key={`${item.rank}-${item.title}`}>- {item.title || "未命名资料"} · {item.section_path || "N/A"}</li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>

      <form className="card p-4" onSubmit={handleSubmit}>
        <textarea
          className="input min-h-[88px] resize-none"
          placeholder="请输入查询内容，例如：从控制论视角比较深度学习与系统优化"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="mt-2 flex justify-end">
          <button className="btn-primary" disabled={disabled} type="submit">
            {loading ? "处理中..." : "查询"}
          </button>
        </div>
      </form>
    </section>
  );
}

export default ChatTab;
