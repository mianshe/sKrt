from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


@dataclass(frozen=True)
class SupabaseStorageConfig:
    url: str
    service_role_key: str
    bucket: str

    @staticmethod
    def from_env() -> Optional["SupabaseStorageConfig"]:
        url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
        key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        bucket = (os.getenv("SUPABASE_STORAGE_BUCKET") or "documents").strip()
        if not url or not key or not bucket:
            return None
        return SupabaseStorageConfig(url=url, service_role_key=key, bucket=bucket)


def supabase_uri(bucket: str, key: str) -> str:
    return f"supabase://{bucket}/{key.lstrip('/')}"


def parse_supabase_uri(uri: str) -> Optional[tuple[str, str]]:
    s = (uri or "").strip()
    if not s.startswith("supabase://"):
        return None
    rest = s[len("supabase://") :]
    if "/" not in rest:
        return None
    bucket, key = rest.split("/", 1)
    if not bucket or not key:
        return None
    return bucket, key


async def upload_file(cfg: SupabaseStorageConfig, *, key: str, file_path: Path, content_type: str = "application/octet-stream") -> str:
    """
    Upload local file to Supabase Storage using service role key.
    Returns a supabase://bucket/key URI for persistence.
    """
    object_key = key.lstrip("/")
    endpoint = f"{cfg.url}/storage/v1/object/{cfg.bucket}/{object_key}"
    headers = {
        "authorization": f"Bearer {cfg.service_role_key}",
        "apikey": cfg.service_role_key,
        "content-type": content_type,
        "x-upsert": "true",
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        with file_path.open("rb") as f:
            resp = await client.post(endpoint, headers=headers, content=f)
    resp.raise_for_status()
    return supabase_uri(cfg.bucket, object_key)


async def download_to_file(cfg: SupabaseStorageConfig, *, bucket: str, key: str, dest_path: Path) -> None:
    endpoint = f"{cfg.url}/storage/v1/object/{bucket}/{key.lstrip('/')}"
    headers = {
        "authorization": f"Bearer {cfg.service_role_key}",
        "apikey": cfg.service_role_key,
    }
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.get(endpoint, headers=headers)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)

