export type SummaryCitation = {
  title: string;
  discipline: string;
  section_path: string;
};

export type DocumentTreeNode = {
  title: string;
  page_start?: number;
  page_end?: number;
  level?: number;
  source?: string;
};

export type ReportProfile = {
  kind?: string;
  label?: string;
  section_blueprint?: string[];
};

export type ChapterSummary = {
  chapter_key?: string;
  chapter_title: string;
  page_start?: number;
  page_end?: number;
  content?: string;
  sections?: Array<{ title: string; content: string }>;
};

export type SummaryLengthStats = {
  highlights?: { count?: number; chars?: number };
  conclusions?: { count?: number; chars?: number };
  actions?: { count?: number; chars?: number };
};

export type SummaryCoverageStats = {
  mode?: string;
  processed_rows?: number;
  total_rows?: number;
  map_groups?: number;
  chapter_groups?: number;
  estimated_total?: number;
  candidate_rows?: number;
  coverage_ratio?: number;
};

export type SummaryEffectiveCoverage = {
  estimated_total?: number;
  candidate_rows?: number;
  coverage_ratio?: number;
};

/** 深度报告等接口可能随 payload 返回的扩展元数据 */
export type SummaryPayloadMetadata = {
  teaching_quality_metrics?: {
    teaching_score?: number;
    concept_coverage?: number;
    teaching_completeness?: number;
  };
};

export type SummaryPayload = {
  output_mode?: "summary" | "report";
  highlights: string[];
  conclusions: string[];
  actions: string[];
  report?: string;
  sections?: Array<{ title: string; content: string }>;
  citations: SummaryCitation[];
  provider?: string;
  fallback?: boolean;
  parse_hits?: number;
  context_len?: number;
  summary_compact_level?: number;
  raw_lengths?: SummaryLengthStats;
  clipped_lengths?: SummaryLengthStats;
  effective_coverage?: SummaryEffectiveCoverage;
  coverage_stats?: SummaryCoverageStats;
  fallback_reason?: "no_results" | "parse_failed" | "normalize_failed" | "none" | string;
  source_quality?: "high" | "medium" | "low" | string;
  report_profile?: ReportProfile;
  document_tree?: DocumentTreeNode[];
  chapter_summaries?: ChapterSummary[];
  metadata?: SummaryPayloadMetadata;
};

type Props = {
  summary: SummaryPayload | null;
  loading: boolean;
  error: string;
  onGenerate: () => void;
  disabled: boolean;
};

function SummaryCards({ summary, loading, error, onGenerate, disabled }: Props) {
  return (
    <div className="card p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-indigo-600">深度分析</h3>
          <p className="text-[11px] text-slate-500">当前端点：`/insights/report`</p>
        </div>
        <button className="btn-primary" disabled={disabled || loading} onClick={onGenerate} type="button">
          {loading ? "生成中..." : "生成报告"}
        </button>
      </div>

      {!summary && !loading && !error && <p className="text-xs text-slate-500">先选择文档，再生成深度分析报告。</p>}
      {error && <p className="text-xs text-rose-600">{error}</p>}

      {summary && (
        <div className="space-y-3">
          <div>
            <p className="text-xs font-semibold text-slate-700">报告正文</p>
            <pre className="mt-1 max-h-[420px] overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 px-2 py-2 text-sm text-slate-700">
              {summary.report || "暂无报告正文"}
            </pre>
          </div>

          <div>
            <p className="text-xs font-semibold text-slate-700">报告分节</p>
            <div className="mt-1 space-y-2">
              {(summary.sections?.length ? summary.sections : [{ title: "分节", content: "暂无分节内容" }]).map((section, idx) => (
                <div key={`sec-${idx}`} className="rounded-lg bg-slate-50 px-2 py-2 text-xs text-slate-700">
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
                : [{ title: "当前未命中有效来源", discipline: "all", section_path: "N/A" }]
              ).map((item, idx) => (
                <div key={`${item.title}-${idx}`} className="rounded-lg bg-slate-50 px-2 py-1 text-xs text-slate-600">
                  来源：{item.title} · {item.section_path} · {item.discipline}
                </div>
              ))}
            </div>
          </div>

          <p className="text-[11px] text-slate-500">
            输出来源：{summary.provider || "unknown"}
            {summary.fallback ? " · 已启用兜底生成" : ""}
          </p>
        </div>
      )}
    </div>
  );
}

export default SummaryCards;
