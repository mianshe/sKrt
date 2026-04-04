import { useEffect, useState } from "react";
import { SummaryPayload } from "../components/SummaryCards";
import {
  downloadDocxBlob,
  exportDocSummaryToDocxBlob,
  exportGlobalSummaryToDocxBlob,
  suggestedDocSummaryFilename,
  suggestedGlobalSummaryFilename,
} from "../lib/exportSummaryDocx";
import { API_BASE } from "../config/apiBase";
import { useAccessToken } from "../lib/auth";
import { useEmbeddingModePreference } from "../lib/embeddingMode";

const TENANT_KEY = "xm_tenant_id";

type DocSummary = {
  document_id: number;
  summary: {
    title: string;
    filename: string;
    document_type: string;
    discipline: string;
    page_count: number;
    chunk_count: number;
    section_count: number;
    top_keywords: string[];
    sections: Array<{
      section_path: string;
      chunk_count: number;
      key_points: string[];
      keywords: string[];
      principles?: string[];
      why?: string[];
      how?: string[];
    }>;
    conclusions: string[];
    principles?: string[];
    why?: string[];
    how?: string[];
  } | null;
};

type Props = {
  refreshKey: string;
};

function KnowledgeTab({ refreshKey }: Props) {
  const summaryCompactLevel = 0;
  const summaryMode: "full" = "full";
  const [embeddingMode, setEmbeddingMode] = useEmbeddingModePreference();
  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);
  const [highlights, setHighlights] = useState<{ items: string[]; conclusions: string[]; actions: string[] } | null>(null);
  const [highlightsLoading, setHighlightsLoading] = useState(false);
  const [highlightsError, setHighlightsError] = useState("");
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState("");
  const [docSummaries, setDocSummaries] = useState<DocSummary[]>([]);
  const [docSummaryLoading, setDocSummaryLoading] = useState(false);
  const [expandedDoc, setExpandedDoc] = useState<number | null>(null);
  const [expandedSection, setExpandedSection] = useState<string | null>(null);

  const normalizeFallbackReason = (reason: unknown): SummaryPayload["fallback_reason"] => {
    return reason === "no_results" || reason === "parse_failed" || reason === "normalize_failed" || reason === "none"
      ? reason
      : "none";
  };
  const normalizeSourceQuality = (quality: unknown): SummaryPayload["source_quality"] => {
    return quality === "high" || quality === "medium" || quality === "low" ? quality : "low";
  };
  const fallbackReasonLabel = (reason: SummaryPayload["fallback_reason"]) => {
    switch (reason) {
      case "no_results":
        return "未命中检索结果";
      case "parse_failed":
        return "模型解析失败";
      case "normalize_failed":
        return "结果规范化失败";
      case "none":
      default:
        return "无";
    }
  };

  // ── 加载全局要点摘要（highlights/conclusions/actions）──────────
  useEffect(() => {
    if (!loggedIn) {
      setHighlights(null);
      setHighlightsError("");
      setHighlightsLoading(false);
      return;
    }
    const controller = new AbortController();
    const tenantId = localStorage.getItem(TENANT_KEY)?.trim() || "public";
    setHighlightsLoading(true);
    setHighlightsError("");
    fetch(`${API_BASE}/insights/summary`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Tenant-Id": tenantId },
      body: JSON.stringify({ query: "全局提炼", discipline: "all", summary_compact_level: summaryCompactLevel, summary_mode: summaryMode, embedding_mode: embeddingMode }),
      signal: controller.signal,
    })
      .then((r) => r.ok ? r.json() : r.json().catch(() => ({})).then((b: any) => Promise.reject(new Error(b?.detail || `要点摘要请求失败 (${r.status})`))))
      .then((data) => {
        setHighlights({
          items: Array.isArray(data.highlights) ? data.highlights : [],
          conclusions: Array.isArray(data.conclusions) ? data.conclusions : [],
          actions: Array.isArray(data.actions) ? data.actions : [],
        });
      })
      .catch((err) => {
        if ((err as DOMException)?.name !== "AbortError") setHighlightsError(err instanceof Error ? err.message : "要点摘要请求失败");
      })
      .finally(() => { if (!controller.signal.aborted) setHighlightsLoading(false); });
    return () => controller.abort();
  }, [embeddingMode, refreshKey, loggedIn]);
  useEffect(() => {
    if (!loggedIn) {
      setDocSummaries([]);
      setDocSummaryLoading(false);
      return;
    }
    const tenantId = localStorage.getItem(TENANT_KEY)?.trim() || "public";
    const load = async () => {
      setDocSummaryLoading(true);
      try {
        const listResp = await fetch(`${API_BASE}/documents`, {
          headers: { "X-Tenant-Id": tenantId },
        });
        if (!listResp.ok) return;
        const listData = await listResp.json();
        const docs: Array<{ id: number; has_summary: boolean }> = listData.documents || [];
        const withSummary = docs.filter((d) => d.has_summary);
        const summaries: DocSummary[] = await Promise.all(
          withSummary.map(async (d) => {
            try {
              const r = await fetch(`${API_BASE}/documents/${d.id}/summary`, {
                headers: { "X-Tenant-Id": tenantId },
              });
              if (!r.ok) return { document_id: d.id, summary: null };
              return (await r.json()) as DocSummary;
            } catch {
              return { document_id: d.id, summary: null };
            }
          })
        );
        setDocSummaries(summaries.filter((s) => s.summary));
      } catch {
        /* ignore */
      } finally {
        setDocSummaryLoading(false);
      }
    };
    load();
  }, [refreshKey, loggedIn]);

  useEffect(() => {
    if (!loggedIn) {
      setSummary(null);
      setSummaryError("");
      setSummaryLoading(false);
      return;
    }
    const controller = new AbortController();
    const loadSummary = async () => {
      setSummaryLoading(true);
      setSummaryError("");
      try {
        const tenantId = localStorage.getItem(TENANT_KEY)?.trim() || "public";
        const resp = await fetch(`${API_BASE}/insights/report`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Tenant-Id": tenantId },
          body: JSON.stringify({
            query: "全局提炼",
            discipline: "all",
            summary_compact_level: summaryCompactLevel,
            summary_mode: summaryMode,
            embedding_mode: embeddingMode,
          }),
          signal: controller.signal,
        });
        if (!resp.ok) throw new Error("全局重点提炼请求失败");
        const data = await resp.json();
        setSummary({
            output_mode: "report",
            highlights: [],
            conclusions: [],
            actions: [],
            report: typeof data.report === "string" ? data.report : "",
            sections: Array.isArray(data.sections) ? data.sections : [],
            citations: Array.isArray(data.citations) ? data.citations : [],
            provider: data.provider,
            fallback: Boolean(data.fallback),
            parse_hits: typeof data.parse_hits === "number" ? data.parse_hits : 0,
            context_len: typeof data.context_len === "number" ? data.context_len : 0,
            summary_compact_level: typeof data.summary_compact_level === "number" ? data.summary_compact_level : summaryCompactLevel,
            summary_mode: typeof data.summary_mode === "string" ? data.summary_mode : summaryMode,
            raw_lengths: data.raw_lengths && typeof data.raw_lengths === "object" ? data.raw_lengths : {},
            clipped_lengths: data.clipped_lengths && typeof data.clipped_lengths === "object" ? data.clipped_lengths : {},
            effective_coverage: data.effective_coverage && typeof data.effective_coverage === "object" ? data.effective_coverage : {},
            coverage_stats: data.coverage_stats && typeof data.coverage_stats === "object" ? data.coverage_stats : {},
            fallback_reason: normalizeFallbackReason(data.fallback_reason),
            source_quality: normalizeSourceQuality(data.source_quality),
          });
      } catch (err) {
        if ((err as DOMException)?.name !== "AbortError") {
          setSummary(null);
          setSummaryError(err instanceof Error ? err.message : "全局重点提炼请求失败，请稍后重试。");
        }
      } finally {
        if (!controller.signal.aborted) {
          setSummaryLoading(false);
        }
      }
    };

    void loadSummary();
    return () => controller.abort();
  }, [embeddingMode, refreshKey, loggedIn]);

  return (
    <section className="space-y-3">
      <div className="card p-4">
        <div className="mb-2 flex flex-wrap gap-2 text-xs">
          <button
            type="button"
            className={`rounded-full px-3 py-1 ${embeddingMode === "auto" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => setEmbeddingMode("auto")}
          >
            自动向量
          </button>
          <button
            type="button"
            className={`rounded-full px-3 py-1 ${embeddingMode === "local" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => setEmbeddingMode("local")}
          >
            本地向量
          </button>
          <button
            type="button"
            className={`rounded-full px-3 py-1 ${embeddingMode === "api" ? "bg-violet-600 text-white" : "bg-slate-100 text-slate-700"}`}
            onClick={() => setEmbeddingMode("api")}
          >
            API 向量
          </button>
        </div>
        <p className="mb-2 text-xs text-amber-700">全局总结和报告会按当前向量模式检索；模式切换后旧索引可能不再命中。</p>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold text-violet-600">✦ 要点总结</h2>
        </div>
        {highlightsLoading && <p className="text-xs text-slate-500">全局要点提炼中...</p>}
        {highlightsError && <p className="text-xs text-rose-600">{highlightsError}</p>}
        {!highlightsLoading && !highlightsError && highlights && (
          <div className="space-y-3">
            {highlights.items.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-slate-700">要点</p>
                <ul className="mt-1 space-y-1 text-sm text-slate-700">
                  {highlights.items.map((item, idx) => <li key={`h-${idx}`}>- {item}</li>)}
                </ul>
              </div>
            )}
            {highlights.conclusions.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-slate-700">结论</p>
                <ul className="mt-1 space-y-1 text-sm text-slate-700">
                  {highlights.conclusions.map((item, idx) => <li key={`c-${idx}`}>- {item}</li>)}
                </ul>
              </div>
            )}
            {highlights.actions.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-slate-700">行动建议</p>
                <ul className="mt-1 space-y-1 text-sm text-slate-700">
                  {highlights.actions.map((item, idx) => <li key={`a-${idx}`}>- {item}</li>)}
                </ul>
              </div>
            )}
            {highlights.items.length === 0 && highlights.conclusions.length === 0 && highlights.actions.length === 0 && (
              <p className="text-xs text-slate-500">暂无要点，请先上传文档。</p>
            )}
          </div>
        )}
        {!highlightsLoading && !highlightsError && !highlights && (
          <p className="text-xs text-slate-500">暂无要点，请先上传文档。</p>
        )}
      </div>

      <div className="card p-4">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-violet-600">✦ 重点提炼</h3>
          {!summaryLoading && summary && (
            <button
              type="button"
              className="rounded-lg border border-violet-200 bg-white px-2 py-1 text-[11px] font-medium text-violet-700 shadow-sm hover:bg-violet-50"
              onClick={async () => {
                try {
                  const blob = await exportGlobalSummaryToDocxBlob(summary);
                  downloadDocxBlob(blob, suggestedGlobalSummaryFilename());
                } catch (e) {
                  console.error(e);
                }
              }}
            >
              下载 Word
            </button>
          )}
        </div>

        {summaryLoading && <p className="text-xs text-slate-500">全局重点提炼中...</p>}
        {summaryError && <p className="text-xs text-rose-600">{summaryError}</p>}

        {!summaryLoading && !summaryError && !summary && <p className="text-xs text-slate-500">当前暂无可展示的重点结果。</p>}

        {!summaryLoading && !summaryError && summary && (
          <div className="space-y-3">
            <div>
              <p className="text-xs font-semibold text-slate-700">报告正文</p>
              <pre className="mt-1 max-h-[480px] overflow-auto whitespace-pre-wrap rounded-xl bg-violet-50/60 px-2 py-2 text-sm text-slate-700 ring-1 ring-violet-100">
                {summary.report || "暂无报告正文"}
              </pre>
            </div>
            <div>
              <p className="text-xs font-semibold text-slate-700">报告分节</p>
              <div className="mt-1 space-y-2">
                {(summary.sections?.length ? summary.sections : [{ title: "分节", content: "暂无分节内容" }]).map((section, idx) => (
                  <div key={`report-sec-${idx}`} className="rounded-xl bg-gradient-to-r from-white to-violet-50/70 px-2 py-2 text-xs text-slate-700 ring-1 ring-violet-100">
                    <p className="font-semibold">{section.title}</p>
                    <p className="mt-1 whitespace-pre-wrap">{section.content}</p>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <p className="text-xs font-semibold text-slate-700">引用来源</p>
              <div className="mt-1 grid gap-1">
                {(summary.citations.length
                  ? summary.citations
                  : [{ title: "基于当前检索未命中", discipline: "all", section_path: "N/A" }]
                ).map((s, idx) => (
                  <div key={`${s.title}-${idx}`} className="rounded-xl bg-violet-50/60 px-2 py-1 text-xs text-slate-600 ring-1 ring-violet-100">
                    来源：{s.title} · {s.section_path} · {s.discipline}
                  </div>
                ))}
              </div>
            </div>
            <div className="rounded-xl border border-violet-100 bg-violet-50/45 px-2 py-2">
              <p className="text-xs font-semibold text-slate-700">模型解析状态</p>
              <p className="mt-1 text-[11px] text-slate-600">
                命中数：{summary.parse_hits ?? 0} · 上下文规模：{summary.context_len ?? 0} 字符
              </p>
              <p className="text-[11px] text-slate-600">
                兜底原因：{fallbackReasonLabel(summary.fallback_reason ?? "none")} · 来源质量：
                {summary.source_quality ?? "unknown"}
              </p>
              <p className="text-[11px] text-slate-600">内容缩减量级别：{summary.summary_compact_level ?? summaryCompactLevel}</p>
              <p className="text-[11px] text-slate-600">
                {summary.output_mode === "report" ? "输出模式：report" : `摘要模式：${summary.summary_mode || summaryMode}`}
              </p>
            </div>
            <div className="rounded-xl border border-violet-100 bg-white/70 px-2 py-2">
              <p className="text-xs font-semibold text-slate-700">长度统计（原始 - 返回）</p>
              <p className="mt-1 text-[11px] text-slate-600">
                要点：{summary.raw_lengths?.highlights?.count ?? 0} 条 / {summary.raw_lengths?.highlights?.chars ?? 0} 字 -{" "}
                {summary.clipped_lengths?.highlights?.count ?? 0} 条 / {summary.clipped_lengths?.highlights?.chars ?? 0} 字
              </p>
              <p className="text-[11px] text-slate-600">
                结论：{summary.raw_lengths?.conclusions?.count ?? 0} 条 / {summary.raw_lengths?.conclusions?.chars ?? 0} 字 -{" "}
                {summary.clipped_lengths?.conclusions?.count ?? 0} 条 / {summary.clipped_lengths?.conclusions?.chars ?? 0} 字
              </p>
              <p className="text-[11px] text-slate-600">
                行动：{summary.raw_lengths?.actions?.count ?? 0} 条 / {summary.raw_lengths?.actions?.chars ?? 0} 字 -{" "}
                {summary.clipped_lengths?.actions?.count ?? 0} 条 / {summary.clipped_lengths?.actions?.chars ?? 0} 字
              </p>
              <p className="text-[11px] text-slate-600">
                map覆盖：{summary.effective_coverage?.candidate_rows ?? 0} / {summary.effective_coverage?.estimated_total ?? 0}
                （{(((summary.effective_coverage?.coverage_ratio ?? 0) as number) * 100).toFixed(1)}%）
              </p>
              <p className="text-[11px] text-slate-600">
                全局覆盖：{summary.coverage_stats?.candidate_rows ?? summary.coverage_stats?.processed_rows ?? 0} /{" "}
                {summary.coverage_stats?.estimated_total ?? summary.coverage_stats?.total_rows ?? 0}
                （{(((summary.coverage_stats?.coverage_ratio ?? 0) as number) * 100).toFixed(1)}%）
              </p>
            </div>
          </div>
        )}
      </div>

      {/* ── 文档级结构化知识摘要 ──────────────────────────────────── */}
      <div className="card p-4">
        <h3 className="mb-2 text-sm font-semibold text-violet-600">✦ 文档知识提取</h3>
        {docSummaryLoading && <p className="text-xs text-slate-500">加载文档摘要中...</p>}
        {!docSummaryLoading && docSummaries.length === 0 && (
          <p className="text-xs text-slate-500">暂无已解析的文档摘要，请先上传文档。</p>
        )}
        <div className="space-y-3">
          {docSummaries.map((ds) => {
            const s = ds.summary!;
            const isExpanded = expandedDoc === ds.document_id;
            return (
              <div key={ds.document_id} className="rounded-2xl bg-gradient-to-r from-white to-violet-50/70 ring-1 ring-violet-100">
                <div className="flex items-start gap-2 px-3 py-2">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => setExpandedDoc(isExpanded ? null : ds.document_id)}
                  >
                    <p className="text-sm font-medium text-slate-800 truncate">{s.title || s.filename}</p>
                    <p className="text-[11px] text-slate-500">
                      {s.document_type} · {s.discipline} · {s.page_count}页 · {s.chunk_count}块 · {s.section_count}节
                    </p>
                    {s.top_keywords.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {s.top_keywords.slice(0, 12).map((kw) => (
                          <span key={kw} className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] text-violet-700">{kw}</span>
                        ))}
                      </div>
                    )}
                  </button>
                  <button
                    type="button"
                    className="shrink-0 rounded-lg border border-violet-200 bg-white px-2 py-1 text-[10px] font-medium text-violet-700 shadow-sm hover:bg-violet-50"
                    title="下载本文摘要为 Word"
                    onClick={async (e) => {
                      e.stopPropagation();
                      try {
                        const blob = await exportDocSummaryToDocxBlob(ds.document_id, s);
                        downloadDocxBlob(blob, suggestedDocSummaryFilename(ds.document_id, s));
                      } catch (err) {
                        console.error(err);
                      }
                    }}
                  >
                    下载 Word
                  </button>
                </div>
                {isExpanded && (
                  <div className="space-y-3 px-3 pb-3">
                    {/* 结论 / 要点 */}
                    {s.conclusions.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-slate-700">核心要点</p>
                        <ul className="mt-1 space-y-0.5">
                          {s.conclusions.map((c, i) => (
                            <li key={i} className="text-xs text-slate-700">• {c}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {/* 原理 */}
                    {(s.principles?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-emerald-700">原理 / 定理 / 定义</p>
                        <ul className="mt-1 space-y-0.5">
                          {s.principles!.map((p, i) => (
                            <li key={i} className="text-xs text-slate-700">• {p}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {/* 为什么 */}
                    {(s.why?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-amber-700">为什么 / 原因 / 意义</p>
                        <ul className="mt-1 space-y-0.5">
                          {s.why!.map((w, i) => (
                            <li key={i} className="text-xs text-slate-700">• {w}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {/* 怎么做 */}
                    {(s.how?.length ?? 0) > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-blue-700">怎么做 / 方法 / 步骤</p>
                        <ul className="mt-1 space-y-0.5">
                          {s.how!.map((h, i) => (
                            <li key={i} className="text-xs text-slate-700">• {h}</li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {/* 按章节展开 */}
                    {s.sections.length > 0 && (
                      <div>
                        <p className="text-xs font-semibold text-slate-700">按章节详情</p>
                        <div className="mt-1 space-y-1">
                          {s.sections.map((sec) => {
                            const secKey = `${ds.document_id}:${sec.section_path}`;
                            const secOpen = expandedSection === secKey;
                            return (
                              <div key={secKey} className="rounded-xl bg-white/80 ring-1 ring-violet-50">
                                <button
                                  className="w-full px-2 py-1 text-left text-[11px] text-slate-600"
                                  onClick={() => setExpandedSection(secOpen ? null : secKey)}
                                >
                                  {sec.section_path} ({sec.chunk_count}块, {sec.key_points.length}要点)
                                </button>
                                {secOpen && (
                                  <div className="space-y-1 px-2 pb-2">
                                    {sec.key_points.length > 0 && (
                                      <div>
                                        <p className="text-[10px] font-semibold text-slate-600">知识点</p>
                                        {sec.key_points.map((kp, i) => (
                                          <p key={i} className="text-[11px] text-slate-600">• {kp}</p>
                                        ))}
                                      </div>
                                    )}
                                    {(sec.principles?.length ?? 0) > 0 && (
                                      <div>
                                        <p className="text-[10px] font-semibold text-emerald-600">原理</p>
                                        {sec.principles!.map((p, i) => (
                                          <p key={i} className="text-[11px] text-slate-600">• {p}</p>
                                        ))}
                                      </div>
                                    )}
                                    {(sec.why?.length ?? 0) > 0 && (
                                      <div>
                                        <p className="text-[10px] font-semibold text-amber-600">为什么</p>
                                        {sec.why!.map((w, i) => (
                                          <p key={i} className="text-[11px] text-slate-600">• {w}</p>
                                        ))}
                                      </div>
                                    )}
                                    {(sec.how?.length ?? 0) > 0 && (
                                      <div>
                                        <p className="text-[10px] font-semibold text-blue-600">怎么做</p>
                                        {sec.how!.map((h, i) => (
                                          <p key={i} className="text-[11px] text-slate-600">• {h}</p>
                                        ))}
                                      </div>
                                    )}
                                    {sec.keywords.length > 0 && (
                                      <div className="flex flex-wrap gap-1">
                                        {sec.keywords.map((kw) => (
                                          <span key={kw} className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[9px] text-slate-600">{kw}</span>
                                        ))}
                                      </div>
                                    )}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

    </section>
  );
}

export default KnowledgeTab;
