from pathlib import Path
p = Path("backend/main.py")
t = p.read_text(encoding="utf-8")
if "download_document_original" not in t:
    t = t.replace(
        "from fastapi.responses import JSONResponse",
        "from fastapi.responses import FileResponse, JSONResponse",
        1,
    )
    t = t.replace(
        "from pydantic import BaseModel, Field",
        "from pydantic import BaseModel, Field\nfrom starlette.background import BackgroundTask",
        1,
    )
    marker = '@app.get("/documents/{doc_id}/summary")'
    insert = """

def _unlink_quiet(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


@app.get("/documents/{doc_id}/original")
async def download_document_original(doc_id: int, request: Request) -> Any:
    identity = _ingest_identity_or_raise(request)
    _require_permission(identity, "tenant.documents.read")
    if local_auth_service.is_anonymous_local_guest(identity):
        raise HTTPException(status_code=404, detail="文档不存在")
    tenant_id = str(identity.get("tenant_id", "public"))
    conn = _conn()
    try:
        row = conn.execute(
            \"\"\"
            SELECT file_path, filename FROM upload_tasks
            WHERE tenant_id = ? AND document_id = ? AND IFNULL(status, '') != 'failed'
            ORDER BY id DESC LIMIT 1
            \"\"\",
            (tenant_id, doc_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(
            status_code=404,
            detail="未找到与该文档关联的上传任务原件（可能为旧版同步上传路径，仅云端无副本）",
        )
    fp = str(row["file_path"] or "").strip()
    filename = str(row["filename"] or "document").strip() or "document"
    if not fp:
        raise HTTPException(status_code=404, detail="原件路径为空")
    if fp.startswith("r2://"):
        r2_cfg = R2StorageConfig.from_env()
        if not r2_cfg:
            raise HTTPException(status_code=503, detail="未配置 R2，无法下载远端原件")
        parsed = parse_r2_uri(fp)
        if not parsed:
            raise HTTPException(status_code=500, detail="无效的 r2:// 路径")
        bucket, key = parsed
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix)
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            r2_download_to_file(r2_cfg, bucket=bucket, key=key, dest_path=tmp_path)
        except Exception as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise HTTPException(status_code=502, detail=f"从对象存储下载失败: {exc}") from exc
        return FileResponse(
            str(tmp_path),
            filename=filename,
            media_type="application/octet-stream",
            background=BackgroundTask(_unlink_quiet, str(tmp_path)),
        )
    if fp.startswith("supabase://"):
        raise HTTPException(status_code=501, detail="Supabase 原件下载未实现")
    p = Path(fp)
    if not p.is_absolute():
        p = UPLOAD_DIR / p.name
    try:
        p = p.resolve()
        p.relative_to(UPLOAD_DIR.resolve())
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail="禁止访问该路径") from None
    if not p.is_file():
        raise HTTPException(status_code=404, detail="原件文件不存在或已清理")
    return FileResponse(p, filename=filename, media_type="application/octet-stream")


"""
    t = t.replace(marker, insert + marker, 1)
    p.write_text(t, encoding="utf-8")
    print("patched main")
else:
    print("main already has endpoint")
