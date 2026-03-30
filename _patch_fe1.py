from pathlib import Path

p = Path("frontend/src/lib/localUserBackup.ts")
t = p.read_text(encoding="utf-8")
if "localDraftProcessJson" not in t:
    old = "export type LocalProcessSnapshot = {"
    new = """export function localDraftProcessJson(): string {
  return JSON.stringify({ version: 1, kind: \"local_draft\", savedAt: new Date().toISOString() });
}

export type LocalProcessSnapshot = {"""
    t = t.replace(old, new, 1)
    p.write_text(t, encoding="utf-8")
    print("localUserBackup ok")

p = Path("frontend/src/hooks/useDocuments.ts")
t = p.read_text(encoding="utf-8")
t = t.replace("const CHUNK_UPLOAD_THRESHOLD = 1024 * 1024;", "export const CHUNK_UPLOAD_THRESHOLD = 1024 * 1024;", 1)
if "downloadCloudDocumentOriginal" not in t:
    ins = """
export async function downloadCloudDocumentOriginal(docId: number, filename: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/documents/${docId}/original`, {
    headers: withTenantHeaders(),
    credentials: "include",
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(t || "下载失败");
  }
  const blob = await resp.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename.replace(/[\\\\/]/g, "_");
  a.click();
  URL.revokeObjectURL(a.href);
}

"""
    t = t.replace("export async function buildLocalProcessSnapshot", ins + "export async function buildLocalProcessSnapshot", 1)
    p.write_text(t, encoding="utf-8")
    print("useDocuments ok")
else:
    p.write_text(t, encoding="utf-8")
