import asyncio
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.main import DB_PATH, UPLOAD_DIR, _guess_doc_type, _rebuild_kg_relations, upload_ingestion_service

ALLOWED_SUFFIX = {".pdf", ".docx", ".txt", ".md", ".markdown"}


def _backup_db() -> Path:
    src = Path(DB_PATH)
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = backup_dir / f"knowledge_{stamp}.db"
    if src.exists():
        shutil.copy2(src, dst)
    return dst


def _clear_index_tables() -> None:
    conn = upload_ingestion_service._conn()  # noqa: SLF001
    try:
        conn.execute("DELETE FROM vector_ingest_checkpoints")
        conn.execute("DELETE FROM upload_tasks")
        conn.execute("DELETE FROM vectors")
        conn.execute("DELETE FROM documents")
        conn.execute("DELETE FROM kg_relations")
        conn.commit()
    finally:
        conn.close()


def _list_upload_files() -> List[Path]:
    root = Path(UPLOAD_DIR)
    if not root.exists():
        return []
    files = [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIX]
    files.sort(key=lambda x: x.name.lower())
    return files


async def main() -> None:
    backup = _backup_db()
    print(f"[rebuild] backup: {backup}")
    _clear_index_tables()
    files = _list_upload_files()
    print(f"[rebuild] files: {len(files)}")
    success = 0
    failed: List[str] = []
    for f in files:
        dtype = _guess_doc_type(f.name)
        task = upload_ingestion_service.create_task(filename=f.name, discipline="all", document_type=dtype)
        tid = int(task.get("id", 0))
        print(f"[rebuild] task={tid} file={f.name} type={dtype}")
        try:
            await upload_ingestion_service.run_task(tid)
            success += 1
        except Exception as exc:
            failed.append(f"{f.name}: {exc}")
            print(f"[rebuild][warn] skip file={f.name} reason={exc}")
    await _rebuild_kg_relations()
    print(f"[rebuild] done success={success} failed={len(failed)}")
    for item in failed:
        print(f"[rebuild][failed] {item}")


if __name__ == "__main__":
    asyncio.run(main())
