import { useEffect, useMemo, useState } from "react";
import type { ChapterSummary, DocumentTreeNode, SummaryPayload } from "../components/SummaryCards";
import { API_BASE } from "../config/apiBase";
import { withTenantHeaders } from "../hooks/useDocuments";
import type { DocumentItem } from "../hooks/useDocuments";
import { useAccessToken } from "../lib/auth";
import { formatApiFetchError } from "../lib/fetchErrors";
import { FileText, Sparkles, ChevronRight, BookOpen, RefreshCw } from "lucide-react";
import { downloadDocxBlob, exportGlobalSummaryToDocxBlob } from "../lib/exportSummaryDocx";

type Props = {
  refreshKey: string;
  documents: DocumentItem[];
};

type ReportSection = { 
  title: string; 
  content: string;
  metadata?: {
    comprehensive_analysis?: string;
    comprehensive_strategy?: string;
  }
};

type ChapterNavNode = {
  navKey: string;
  title: string;
  level: number;
  page_start?: number;
  page_end?: number;
  matchedSummaries: ChapterSummary[];
};

function suggestedReportFilename(doc: DocumentItem | null): string {
  const raw = (doc?.title || doc?.filename || "document").trim();
  const safe = raw.replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_").slice(0, 80) || "document";
  return `${safe}-deep-analysis.docx`;
}

function looksLikeInstitutionTitle(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  if (!normalized) return false;
  return [
    "university",
    "college",
    "faculty",
    "department",
    "school",
    "学院",
    "大学",
    "学校",
    "研究院",
  ].some((term) => normalized.includes(term));
}

function deriveFilenameTitle(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "").trim();
  const pieces = [stem, ...stem.split(/[_-]+/g)]
    .map((part) => part.trim())
    .filter(Boolean);
  let best = stem || "document";
  let bestScore = -999;
  for (const piece of pieces) {
    let score = 0;
    if (/[\u4e00-\u9fff]/.test(piece)) score += 3;
    if (/[A-Za-z]/.test(piece)) score += 1;
    if (/[\u4e00-\u9fff]/.test(piece) && /[A-Za-z]/.test(piece)) score += 1;
    if (piece.length >= 6 && piece.length <= 40) score += 4;
    else if (piece.length > 40 && piece.length <= 80) score += 2;
    if (/(研究|分析|设计|系统|模型|方法|实现|应用|优化|预测|影响|治理|识别|检测|基于)/.test(piece)) score += 4;
    if (/(class|student|grade|学号|班|专业)/i.test(piece)) score -= 3;
    if (looksLikeInstitutionTitle(piece)) score -= 6;
    if (score > bestScore) {
      best = piece;
      bestScore = score;
    }
  }
  return best || "document";
}

function deriveAnalysisSubject(doc: DocumentItem | null): string {
  if (!doc) return "document";
  const title = (doc.title || "").trim();
  if (title && !looksLikeInstitutionTitle(title)) return title;
  return deriveFilenameTitle(doc.filename || title || "document");
}

function normalizeFallbackReason(reason: unknown): SummaryPayload["fallback_reason"] {
  return reason === "no_results" || reason === "parse_failed" || reason === "normalize_failed" || reason === "none"
    ? reason
    : "none";
}

function normalizeSourceQuality(quality: unknown): SummaryPayload["source_quality"] {
  return quality === "high" || quality === "medium" || quality === "low" ? quality : "low";
}

function normalizeReportSections(sections: unknown): ReportSection[] {
  if (!Array.isArray(sections)) return [];
  return sections
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => {
      const section: ReportSection = {
        title: typeof item.title === "string" ? item.title : "Section",
        content: typeof item.content === "string" ? item.content : "",
      };
      
      const metadata = item.metadata;
      if (metadata && typeof metadata === "object") {
        const metadataObj = metadata as Record<string, unknown>;
        section.metadata = {
          comprehensive_analysis: typeof metadataObj.comprehensive_analysis === "string" 
            ? metadataObj.comprehensive_analysis 
            : undefined,
          comprehensive_strategy: typeof metadataObj.comprehensive_strategy === "string" 
            ? metadataObj.comprehensive_strategy 
            : undefined,
        };
      }
      
      return section;
    })
    .filter((item) => item.title.trim() || item.content.trim());
}

function normalizeChapterSummaries(items: unknown): ChapterSummary[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      chapter_key: typeof item.chapter_key === "string" ? item.chapter_key : undefined,
      chapter_title: typeof item.chapter_title === "string" ? item.chapter_title : "Chapter",
      page_start: typeof item.page_start === "number" ? item.page_start : undefined,
      page_end: typeof item.page_end === "number" ? item.page_end : undefined,
      content: typeof item.content === "string" ? item.content : "",
      sections: normalizeReportSections(item.sections),
    }))
    .filter((item) => item.chapter_title.trim().length > 0);
}

function normalizeDocumentTree(items: unknown): DocumentTreeNode[] {
  if (!Array.isArray(items)) return [];
  return items
    .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    .map((item) => ({
      title: typeof item.title === "string" ? item.title : "Chapter",
      page_start: typeof item.page_start === "number" ? item.page_start : undefined,
      page_end: typeof item.page_end === "number" ? item.page_end : undefined,
      level: typeof item.level === "number" ? item.level : 1,
      source: typeof item.source === "string" ? item.source : undefined,
    }))
    .filter((item) => item.title.trim().length > 0);
}

function normalizeComparableTitle(value: string): string {
  return value.toLowerCase().replace(/[\s\-_.:/()[\]【】（）·*★]+/g, "");
}

function chapterNumberPrefix(value: string): string {
  return (value.trim().match(/^(\d+(?:\.\d+)*)/)?.[1] || "").trim();
}

function titlesMatchChapterNode(nodeTitle: string, summaryTitle: string): boolean {
  const normalizedNode = normalizeComparableTitle(nodeTitle);
  const normalizedSummary = normalizeComparableTitle(summaryTitle);
  if (!normalizedNode || !normalizedSummary) return false;
  if (normalizedNode === normalizedSummary) return true;

  const nodePrefix = chapterNumberPrefix(nodeTitle);
  const summaryPrefix = chapterNumberPrefix(summaryTitle);
  if (nodePrefix && summaryPrefix) return nodePrefix === summaryPrefix;

  return false;
}

function deriveTreeRanges(tree: DocumentTreeNode[]): DocumentTreeNode[] {
  return tree.map((node, index) => {
    const start = typeof node.page_start === "number" ? node.page_start : undefined;
    const explicitEnd = typeof node.page_end === "number" ? node.page_end : undefined;
    const nextStart = tree.slice(index + 1).find((item) => typeof item.page_start === "number")?.page_start;
    let derivedEnd = explicitEnd;
    if (typeof start === "number" && typeof nextStart === "number" && nextStart > start) {
      const previousPage = nextStart - 1;
      if (typeof derivedEnd !== "number" || derivedEnd < previousPage) {
        derivedEnd = previousPage;
      }
    }
    if (typeof start === "number" && typeof derivedEnd !== "number") {
      derivedEnd = start;
    }
    return {
      ...node,
      page_start: start,
      page_end: derivedEnd,
    };
  });
}

function rangesOverlap(
  aStart?: number,
  aEnd?: number,
  bStart?: number,
  bEnd?: number
): boolean {
  if (
    typeof aStart !== "number" ||
    typeof aEnd !== "number" ||
    typeof bStart !== "number" ||
    typeof bEnd !== "number"
  ) {
    return false;
  }
  return aStart <= bEnd && bStart <= aEnd;
}

function hasUsablePageRange(start?: number, end?: number): boolean {
  return typeof start === "number" && typeof end === "number" && start > 0 && end >= start;
}

function uniqueChapterSummaries(items: ChapterSummary[]): ChapterSummary[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = [
      item.chapter_key || "",
      item.chapter_title || "",
      item.page_start ?? "",
      item.page_end ?? "",
      item.content?.slice(0, 80) || "",
    ].join("|");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function scoreChapterSummaryMatch(node: DocumentTreeNode, summary: ChapterSummary): number {
  let score = 0;
  const nodeTitle = node.title || "";
  const summaryTitle = summary.chapter_title || "";
  if (normalizeComparableTitle(nodeTitle) === normalizeComparableTitle(summaryTitle)) {
    score += 1000;
  }
  const nodePrefix = chapterNumberPrefix(nodeTitle);
  const summaryPrefix = chapterNumberPrefix(summaryTitle);
  if (nodePrefix && summaryPrefix && nodePrefix === summaryPrefix) {
    score += 900 + Math.min(nodePrefix.length, 20);
  }
  if (
    hasUsablePageRange(node.page_start, node.page_end) &&
    hasUsablePageRange(summary.page_start, summary.page_end) &&
    rangesOverlap(node.page_start, node.page_end, summary.page_start, summary.page_end)
  ) {
    score += 100;
    if (node.page_start === summary.page_start) score += 20;
  }
  return score;
}

function bestChapterSummaryMatches(node: DocumentTreeNode, chapterSummaries: ChapterSummary[]): ChapterSummary[] {
  const scored = uniqueChapterSummaries(chapterSummaries)
    .map((summary) => ({ summary, score: scoreChapterSummaryMatch(node, summary) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
  if (!scored.length) return [];
  const top = scored[0];
  return top ? [top.summary] : [];
}

function buildChapterNavigation(
  documentTree: DocumentTreeNode[],
  chapterSummaries: ChapterSummary[]
): ChapterNavNode[] {
  if (documentTree.length) {
    return deriveTreeRanges(documentTree).map((node, index) => {
      const matchedSummaries = bestChapterSummaryMatches(node, chapterSummaries);
      return {
        navKey: `tree-${index}-${node.title}`,
        title: node.title,
        level: typeof node.level === "number" ? node.level : 1,
        page_start: node.page_start,
        page_end: node.page_end,
        matchedSummaries,
      };
    });
  }

  return chapterSummaries.map((summary, index) => ({
    navKey: `summary-${index}-${summary.chapter_title}`,
    title: summary.chapter_title,
    level: 1,
    page_start: summary.page_start,
    page_end: summary.page_end,
    matchedSummaries: [summary],
  }));
}

function buildDisplayedSections(navNode: ChapterNavNode | null, report: SummaryPayload | null): ReportSection[] {
  const matched = navNode?.matchedSummaries ?? [];
  if (matched.length === 1) {
    const chapterSummary = matched[0];
    if (chapterSummary?.sections?.length) return chapterSummary.sections;
    if (chapterSummary?.content?.trim()) {
      return [{ title: chapterSummary.chapter_title, content: chapterSummary.content.trim() }];
    }
  }
  if (matched.length > 1) {
    const mergedSections = matched.flatMap((summary) => {
      if (summary.sections?.length) {
        return summary.sections.map((section) => ({
          title: `${summary.chapter_title} · ${section.title}`,
          content: section.content,
        }));
      }
      if (summary.content?.trim()) {
        return [{ title: summary.chapter_title, content: summary.content.trim() }];
      }
      return [];
    });
    if (mergedSections.length) {
      return mergedSections;
    }
  }
  if (navNode) {
    return [{
      title: navNode.title,
      content: "这个章节节点暂时没有匹配到独立的章节摘要。请点击“重新生成”刷新深度分析；如果仍然为空，说明当前入库切块没有保留足够的章节边界。",
    }];
  }
  return normalizeReportSections(report?.sections);
}

function KnowledgeTab({ refreshKey, documents }: Props) {
  const accessToken = useAccessToken();
  const loggedIn = Boolean(accessToken);
  const reportCompactLevel = 0;
  const showRedundantBlocks = false;

  const [selectedDocId, setSelectedDocId] = useState<number | null>(null);
  const [activeTreeIndex, setActiveTreeIndex] = useState(0);
  const [report, setReport] = useState<SummaryPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  const selectedDoc = useMemo(
    () => documents.find((doc) => doc.id === selectedDocId) ?? null,
    [documents, selectedDocId]
  );

  const chapterSummaries = useMemo(() => normalizeChapterSummaries(report?.chapter_summaries), [report?.chapter_summaries]);
  const documentTree = useMemo(() => normalizeDocumentTree(report?.document_tree), [report?.document_tree]);
  const chapterNavigation = useMemo(
    () => buildChapterNavigation(documentTree, chapterSummaries),
    [documentTree, chapterSummaries]
  );
  const displayedSections = useMemo(
    () => buildDisplayedSections(chapterNavigation[activeTreeIndex] ?? null, report),
    [activeTreeIndex, chapterNavigation, report]
  );
  const emptyStateMessage = !documents.length
      ? "请先上传并完成解析"
      : "选择文档开始分析";

  useEffect(() => {
    if (!documents.length) {
      setSelectedDocId(null);
      return;
    }
    if (selectedDocId == null || !documents.some((doc) => doc.id === selectedDocId)) {
      setSelectedDocId(documents[0].id);
    }
  }, [documents, selectedDocId]);

  useEffect(() => {
    setReport(null);
    setError("");
    setProgress("");
    setActiveTreeIndex(0);
  }, [selectedDocId]);

  useEffect(() => {
    if (!selectedDocId) return;

    const controller = new AbortController();
    const forceRefresh = reloadToken > 0;
    const loadReport = async () => {
      setLoading(true);
      setError("");
      setProgress(forceRefresh ? "正在重新生成深度分析..." : "");
      try {
        const docLabel = deriveAnalysisSubject(selectedDoc);
        const resp = await fetch(`${API_BASE}/insights/report/stream`, {
          method: "POST",
          headers: withTenantHeaders({
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          }),
          credentials: "include",
          body: JSON.stringify({
            query: `Generate a deep analysis for this document using its actual content as the primary subject. Do not treat the institution on the cover page as the research subject unless the body text clearly says so. Title hint: ${docLabel}`,
            document_id: selectedDocId,
            summary_compact_level: reportCompactLevel,
            force_refresh: forceRefresh,
          }),
          signal: controller.signal,
        });

        if (!resp.ok) {
          const details = await resp.text().catch(() => "");
          throw new Error(details || `分析请求失败 (${resp.status})`);
        }
        const reader = resp.body?.getReader();
        if (!reader) throw new Error("分析响应不可读");

        const decoder = new TextDecoder();
        let buffer = "";
        let receivedDone = false;
        let streamError = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            const event = JSON.parse(line.slice(6));
            if (event.stage === "map" || event.stage === "reduce" || event.stage === "node") {
              setProgress(event.message || event.node || event.stage);
              continue;
            }
            if (event.stage === "error") {
              streamError = typeof event.message === "string" && event.message.trim()
                ? event.message.trim()
                : "深度分析失败";
              continue;
            }
            if (event.stage === "done" && event.result) {
              receivedDone = true;
              const data = event.result;
              setReport({
                output_mode: "report",
                highlights: [], conclusions: [], actions: [],
                report: typeof data.report === "string" ? data.report : "",
                sections: normalizeReportSections(data.sections),
                citations: [], provider: data.provider, fallback: Boolean(data.fallback),
                parse_hits: 0, context_len: 0, summary_compact_level: reportCompactLevel,
                raw_lengths: {}, clipped_lengths: {}, effective_coverage: {},
                coverage_stats: data.coverage_stats ?? {}, fallback_reason: normalizeFallbackReason(data.fallback_reason), source_quality: "low",
                report_profile: data.report_profile ?? {}, document_tree: Array.isArray(data.document_tree) ? data.document_tree : [],
                chapter_summaries: Array.isArray(data.chapter_summaries) ? data.chapter_summaries : [],
              });
              continue;
            }
          }
        }
        if (streamError) throw new Error(streamError);
        if (!receivedDone) throw new Error("深度分析已结束，但没有返回结果");
      } catch (err) {
        if ((err as DOMException)?.name !== "AbortError") {
          setError(formatApiFetchError(err, "深度分析加载失败"));
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    void loadReport();
    return () => controller.abort();
  }, [refreshKey, reloadToken, selectedDocId, selectedDoc]);

  return (
    <div className="flex flex-col gap-8 h-full">
      {/* Top Selector Box */}
      <div className="neo-box bg-white p-6 rotate-1">
        <div className="flex justify-between items-center mb-4">
          <h3 className="text-xl font-black uppercase tracking-tighter flex items-center gap-2">
            <BookOpen size={24} className="text-pink-500" />
            分析视图
          </h3>
          <div className="flex items-center gap-2">
            {selectedDocId && loggedIn && (
              <button
                className="neo-button-sm bg-yellow-400 text-slate-900 flex items-center gap-2 disabled:opacity-60"
                disabled={loading}
                onClick={() => setReloadToken((value) => value + 1)}
              >
                <RefreshCw size={16} className={loading ? "animate-spin" : ""} /> 重新生成
              </button>
            )}
            {report && (
              <button
                className="neo-button-sm bg-blue-400 text-white flex items-center gap-2"
                onClick={async () => {
                  const blob = await exportGlobalSummaryToDocxBlob(report);
                  downloadDocxBlob(blob, suggestedReportFilename(selectedDoc));
                }}
              >
                <FileText size={16} /> 导出DOCX
              </button>
            )}
          </div>
        </div>

        <select
          className="neo-input w-full bg-slate-50 font-bold uppercase text-xs"
          value={selectedDocId ?? ""}
          onChange={(e) => setSelectedDocId(e.target.value ? Number(e.target.value) : null)}
        >
          {documents.map((doc) => (
            <option key={doc.id} value={doc.id}>
              {doc.filename || doc.title}
            </option>
          ))}
        </select>
      </div>

      {loading && (
        <div className="neo-box bg-yellow-400 p-12 flex flex-col items-center justify-center text-center">
          <div className="w-20 h-20 border-8 border-slate-900 border-t-pink-500 rounded-full animate-spin mb-6" />
          <p className="text-2xl font-black uppercase tracking-widest">{progress || "深度扫描中..."}</p>
          <div className="neo-box-sm bg-white w-full max-w-md h-6 mt-8 overflow-hidden">
            <div className="h-full bg-blue-400 animate-pulse w-2/3 border-r-4 border-slate-900" />
          </div>
        </div>
      )}

      {!loading && report && (
        <div className="space-y-8 pb-10">
          {/* Main Summary */}
          <section className="neo-box bg-blue-400 p-8 rotate-[-0.5deg]">
            <h4 className="text-2xl font-black uppercase mb-4 text-white drop-shadow-md flex items-center gap-2">
              <Sparkles size={24} />
              执行摘要
            </h4>
            <div className="neo-box-sm bg-white p-6 text-lg leading-relaxed font-bold">
              {report.report || "No summary available."}
            </div>
          </section>

          {/* Chapters / Sections Navigation */}
          {chapterNavigation.length > 1 && (
            <div className="flex flex-wrap gap-2">
              {chapterNavigation.map((ch, idx) => (
                <button
                  key={ch.navKey}
                  onClick={() => setActiveTreeIndex(idx)}
                  className={`neo-button-sm ${activeTreeIndex === idx ? 'bg-pink-400 text-white' : 'bg-white'}`}
                  style={{ marginLeft: `${Math.max(0, Math.min((ch.level - 1) * 12, 36))}px` }}
                >
                  {ch.title}
                </button>
              ))}
            </div>
          )}

          {/* Detailed Sections */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {displayedSections.map((section, idx) => (
              <article key={idx} className="neo-box bg-white p-6 hover:translate-x-1 hover:translate-y-1 transition-all">
                <div className="inline-block bg-yellow-400 px-3 py-1 neo-box-sm text-[10px] font-black uppercase mb-4">
                  章节 {idx + 1}
                </div>
                <h5 className="text-xl font-black uppercase mb-3 border-b-2 border-slate-900 pb-2">{section.title}</h5>
                <p className="text-sm leading-relaxed font-bold text-slate-700 whitespace-pre-wrap">
                  {section.content}
                </p>
                {showRedundantBlocks && section.metadata?.comprehensive_analysis && (
                  <div className="mt-4 neo-box-sm bg-green-400 p-3 text-[11px] font-black">
                    策略: {section.metadata.comprehensive_analysis}
                  </div>
                )}
              </article>
            ))}
          </div>

          {showRedundantBlocks && (
            <section className="mt-12">
              <h4 className="text-xl font-black uppercase mb-6 flex items-center gap-2">
                <ChevronRight size={24} className="text-pink-500" />
                快速概览
              </h4>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {displayedSections.slice(0, 4).map((s, i) => (
                  <div
                    key={i}
                    className={`neo-box p-4 h-40 flex flex-col justify-between ${
                      i % 2 === 0 ? "bg-yellow-400 rotate-1" : "bg-pink-400 text-white rotate-[-1deg]"
                    }`}
                  >
                    <p className="text-xs font-black uppercase truncate border-b-2 border-current pb-1 mb-2">
                      {s.title}
                    </p>
                    <p className="text-[10px] font-bold line-clamp-4 leading-tight">
                      {s.content}
                    </p>
                    <div className="text-[9px] font-black uppercase opacity-60 mt-2">洞察 #{i + 1}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {!loading && !report && !error && (
        <div className="neo-box bg-slate-50 p-20 flex flex-col items-center justify-center text-center opacity-50">
          <BookOpen size={64} className="mb-4 text-slate-300" />
          <p className="text-xl font-black uppercase">{emptyStateMessage}</p>
        </div>
      )}

      {error && (
        <div className="neo-box bg-red-400 text-white p-6 font-black uppercase rotate-[-1deg]">
          分析错误: {error}
        </div>
      )}
    </div>
  );
}

export default KnowledgeTab;
