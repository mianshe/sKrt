import { Document, HeadingLevel, Packer, Paragraph } from "docx";

import type { SummaryPayload } from "../components/SummaryCards";

/** 与 KnowledgeTab 中 `DocSummary.summary` 一致，用于导出单文档摘要 */
export type DocSummaryContent = {
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
};

function sanitizeFilenameBase(name: string): string {
  return name.replace(/[\\/:*?"<>|]/g, "_").trim() || "export";
}

function formatDateStamp(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

function pushLines(children: Paragraph[], text: string) {
  const lines = text.split(/\r?\n/);
  for (const line of lines) {
    children.push(new Paragraph({ text: line.length ? line : " " }));
  }
}

function pushHeading(children: Paragraph[], text: string, level: (typeof HeadingLevel)[keyof typeof HeadingLevel]) {
  children.push(new Paragraph({ text: text || " ", heading: level }));
}

function pushLabeledList(
  children: Paragraph[],
  label: string,
  items: string[],
  labelHeading: (typeof HeadingLevel)[keyof typeof HeadingLevel] = HeadingLevel.HEADING_2
) {
  const filtered = items.map((s) => s.trim()).filter(Boolean);
  if (filtered.length === 0) return;
  pushHeading(children, label, labelHeading);
  for (const item of filtered) {
    children.push(new Paragraph({ text: `• ${item}` }));
  }
}

export function suggestedGlobalSummaryFilename(): string {
  return `重点提炼-${formatDateStamp()}.docx`;
}

export function suggestedDocSummaryFilename(documentId: number, s: DocSummaryContent): string {
  const base = sanitizeFilenameBase(s.title || s.filename || `doc-${documentId}`);
  return `${base}-doc${documentId}.docx`;
}

export async function exportGlobalSummaryToDocxBlob(payload: SummaryPayload): Promise<Blob> {
  const children: Paragraph[] = [];

  const title =
    payload.output_mode === "report" ? "全局重点报告" : "全局重点总结";
  pushHeading(children, title, HeadingLevel.HEADING_1);

  if (payload.report?.trim()) {
    pushHeading(children, "报告正文", HeadingLevel.HEADING_2);
    pushLines(children, payload.report.trim());
  }

  const secs = payload.sections?.filter((s) => s.title || s.content) ?? [];
  if (secs.length > 0) {
    pushHeading(children, "报告分节", HeadingLevel.HEADING_2);
    for (const sec of secs) {
      pushHeading(children, sec.title || "分节", HeadingLevel.HEADING_2);
      if (sec.content?.trim()) pushLines(children, sec.content.trim());
    }
  }

  pushLabeledList(children, "要点", payload.highlights ?? []);
  pushLabeledList(children, "结论", payload.conclusions ?? []);
  pushLabeledList(children, "行动建议", payload.actions ?? []);

  const cites = payload.citations ?? [];
  if (cites.length > 0) {
    pushHeading(children, "引用来源", HeadingLevel.HEADING_2);
    for (const c of cites) {
      children.push(
        new Paragraph({
          text: `来源：${c.title} · ${c.section_path} · ${c.discipline}`,
        })
      );
    }
  }

  if (children.length <= 1) {
    children.push(new Paragraph({ text: "（暂无正文，请先上传文档并完成解析。）" }));
  }

  const doc = new Document({
    title,
    creator: "xm1-knowledge",
    sections: [{ children }],
  });

  return Packer.toBlob(doc);
}

export async function exportDocSummaryToDocxBlob(documentId: number, s: DocSummaryContent): Promise<Blob> {
  const children: Paragraph[] = [];
  const mainTitle = s.title?.trim() || s.filename?.trim() || `文档 ${documentId}`;
  pushHeading(children, mainTitle, HeadingLevel.HEADING_1);

  children.push(
    new Paragraph({
      text: `类型：${s.document_type} · 学科：${s.discipline} · 页数：${s.page_count} · 分块：${s.chunk_count} · 节数：${s.section_count}`,
    })
  );

  if (s.top_keywords?.length) {
    pushHeading(children, "关键词", HeadingLevel.HEADING_2);
    children.push(new Paragraph({ text: s.top_keywords.join("、") }));
  }

  pushLabeledList(children, "核心要点", s.conclusions ?? []);
  pushLabeledList(children, "原理 / 定理 / 定义", s.principles ?? []);
  pushLabeledList(children, "为什么 / 原因 / 意义", s.why ?? []);
  pushLabeledList(children, "怎么做 / 方法 / 步骤", s.how ?? []);

  if (s.sections?.length) {
    pushHeading(children, "按章节详情", HeadingLevel.HEADING_2);
    for (const sec of s.sections) {
      pushHeading(children, `${sec.section_path}（${sec.chunk_count} 块）`, HeadingLevel.HEADING_2);
      pushLabeledList(children, "知识点", sec.key_points ?? [], HeadingLevel.HEADING_3);
      pushLabeledList(children, "原理", sec.principles ?? [], HeadingLevel.HEADING_3);
      pushLabeledList(children, "为什么", sec.why ?? [], HeadingLevel.HEADING_3);
      pushLabeledList(children, "怎么做", sec.how ?? [], HeadingLevel.HEADING_3);
      if (sec.keywords?.length) {
        children.push(new Paragraph({ text: `关键词：${sec.keywords.join("、")}` }));
      }
    }
  }

  const doc = new Document({
    title: mainTitle,
    creator: "xm1-knowledge",
    sections: [{ children }],
  });

  return Packer.toBlob(doc);
}

/** 触发浏览器下载 .docx */
export function downloadDocxBlob(blob: Blob, filename: string): void {
  const raw = filename.trim() || "export.docx";
  const base = raw.replace(/\.docx$/i, "").trim() || "export";
  const safe = `${sanitizeFilenameBase(base)}.docx`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = safe;
  a.click();
  URL.revokeObjectURL(url);
}
