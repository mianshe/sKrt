export type SummaryCitation = {
  title: string;
  discipline: string;
  section_path: string;
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
  estimated_total?: number;
  candidate_rows?: number;
  coverage_ratio?: number;
};

export type SummaryEffectiveCoverage = {
  estimated_total?: number;
  candidate_rows?: number;
  coverage_ratio?: number;
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
  summary_mode?: string;
  raw_lengths?: SummaryLengthStats;
  clipped_lengths?: SummaryLengthStats;
  effective_coverage?: SummaryEffectiveCoverage;
  coverage_stats?: SummaryCoverageStats;
  fallback_reason?: "no_results" | "parse_failed" | "normalize_failed" | "none" | string;
  source_quality?: "high" | "medium" | "low" | string;
};

type Props = {
  summary: SummaryPayload | null;
  loading: boolean;
  error: string;
  onGenerate: () => void;
  disabled: boolean;
};

function SummaryCards({ summary, loading, error, onGenerate, disabled }: Props) {
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

  return (
    <div className="card p-3">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-indigo-600">{summary?.output_mode === "report" ? "重点报告" : "重点总结"}</h3>
          <p className="text-[11px] text-slate-500">
            当前端点：{summary?.output_mode === "report" ? "/insights/report" : "/insights/summary"}
          </p>
        </div>
        <button className="btn-primary" disabled={disabled || loading} onClick={onGenerate} type="button">
          {loading ? "生成中..." : summary?.output_mode === "report" ? "生成报告" : "生成重点"}
        </button>
      </div>

      {!summary && !loading && !error && <p className="text-xs text-slate-500">先完成一次问答，再生成要点/结论/行动建议。</p>}
      {error && <p className="text-xs text-rose-600">{error}</p>}

      {summary && (
        <div className="space-y-3">
          {summary.output_mode === "report" && (
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
                  {(summary.sections?.length ? summary.sections : [{ title: "分节", content: "暂无分节内容" }]).map((s, idx) => (
                    <div key={`sec-${idx}`} className="rounded-lg bg-slate-50 px-2 py-2 text-xs text-slate-700">
                      <p className="font-semibold">{s.title}</p>
                      <p className="mt-1 whitespace-pre-wrap">{s.content}</p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
          {summary.output_mode !== "report" && (
            <>
              <div>
                <p className="text-xs font-semibold text-slate-700">要点</p>
                <ul className="mt-1 space-y-1 text-sm text-slate-700">
                  {summary.highlights.map((item, idx) => (
                    <li key={`h-${idx}`}>- {item}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-700">结论</p>
                <ul className="mt-1 space-y-1 text-sm text-slate-700">
                  {summary.conclusions.map((item, idx) => (
                    <li key={`c-${idx}`}>- {item}</li>
                  ))}
                </ul>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-700">行动建议</p>
                <ul className="mt-1 space-y-1 text-sm text-slate-700">
                  {summary.actions.map((item, idx) => (
                    <li key={`a-${idx}`}>- {item}</li>
                  ))}
                </ul>
              </div>
            </>
          )}
          <div>
            <p className="text-xs font-semibold text-slate-700">引用来源</p>
            <div className="mt-1 grid gap-1">
              {(summary.citations.length ? summary.citations : [{ title: "基于当前检索未命中", discipline: "all", section_path: "N/A" }]).map((s, idx) => (
                <div key={`${s.title}-${idx}`} className="rounded-lg bg-slate-50 px-2 py-1 text-xs text-slate-600">
                  来源：{s.title} · {s.section_path} · {s.discipline}
                </div>
              ))}
            </div>
          </div>
          <p className="text-[11px] text-slate-500">
            输出来源：{summary.provider || "unknown"} {summary.fallback ? "· 已启用规则兜底" : ""}
          </p>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-2">
            <p className="text-xs font-semibold text-slate-700">模型解析状态</p>
            <p className="mt-1 text-[11px] text-slate-600">
              命中数：{summary.parse_hits ?? 0} · 上下文规模：{summary.context_len ?? 0} 字符
            </p>
            <p className="text-[11px] text-slate-600">
              兜底原因：{fallbackReasonLabel(summary.fallback_reason ?? "none")} · 来源质量：{summary.source_quality ?? "unknown"}
            </p>
            <p className="text-[11px] text-slate-600">内容缩减量级别：{summary.summary_compact_level ?? 1}</p>
            <p className="text-[11px] text-slate-600">
              输出模式：{summary.output_mode || "summary"} · 摘要模式：{summary.summary_mode || "fast"}
            </p>
          </div>
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-2">
            <p className="text-xs font-semibold text-slate-700">长度统计（原始 → 返回）</p>
            <p className="mt-1 text-[11px] text-slate-600">
              要点：{summary.raw_lengths?.highlights?.count ?? 0} 条 / {summary.raw_lengths?.highlights?.chars ?? 0} 字 →{" "}
              {summary.clipped_lengths?.highlights?.count ?? 0} 条 / {summary.clipped_lengths?.highlights?.chars ?? 0} 字
            </p>
            <p className="text-[11px] text-slate-600">
              结论：{summary.raw_lengths?.conclusions?.count ?? 0} 条 / {summary.raw_lengths?.conclusions?.chars ?? 0} 字 →{" "}
              {summary.clipped_lengths?.conclusions?.count ?? 0} 条 / {summary.clipped_lengths?.conclusions?.chars ?? 0} 字
            </p>
            <p className="text-[11px] text-slate-600">
              行动：{summary.raw_lengths?.actions?.count ?? 0} 条 / {summary.raw_lengths?.actions?.chars ?? 0} 字 →{" "}
              {summary.clipped_lengths?.actions?.count ?? 0} 条 / {summary.clipped_lengths?.actions?.chars ?? 0} 字
            </p>
            <p className="mt-1 text-[11px] text-slate-600">
              map覆盖：{summary.effective_coverage?.candidate_rows ?? 0} / {summary.effective_coverage?.estimated_total ?? 0}
              （{(((summary.effective_coverage?.coverage_ratio ?? 0) as number) * 100).toFixed(1)}%）
            </p>
            <p className="text-[11px] text-slate-600">
              全局覆盖：{summary.coverage_stats?.processed_rows ?? 0} / {summary.coverage_stats?.total_rows ?? 0}
              （{(((summary.coverage_stats?.coverage_ratio ?? 0) as number) * 100).toFixed(1)}%） · 分组：
              {summary.coverage_stats?.map_groups ?? 0}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default SummaryCards;
