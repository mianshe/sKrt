from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _clean_env_value(name: str) -> str:
    raw = str(os.getenv(name) or "").strip()
    if raw.lower() in {"none", "null", "nil", "undefined"}:
        return ""
    return raw


@dataclass(frozen=True)
class R2StorageConfig:
    endpoint: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    region: str = "auto"

    @staticmethod
    def from_env() -> Optional["R2StorageConfig"]:
        enabled = _clean_env_value("R2_ENABLED").lower() or "1"
        if enabled in {"0", "false", "off", "no"}:
            return None
        endpoint = _clean_env_value("R2_ENDPOINT").rstrip("/")
        bucket = _clean_env_value("R2_BUCKET")
        access_key_id = _clean_env_value("R2_ACCESS_KEY_ID")
        secret_access_key = _clean_env_value("R2_SECRET_ACCESS_KEY")
        region = _clean_env_value("R2_REGION") or "auto"
        if not endpoint or not bucket or not access_key_id or not secret_access_key:
            return None
        return R2StorageConfig(
            endpoint=endpoint,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=region,
        )


def r2_uri(bucket: str, key: str) -> str:
    return f"r2://{bucket}/{key.lstrip('/')}"


def parse_r2_uri(uri: str) -> Optional[Tuple[str, str]]:
    s = (uri or "").strip()
    if not s.startswith("r2://"):
        return None
    rest = s[len("r2://") :]
    if "/" not in rest:
        return None
    bucket, key = rest.split("/", 1)
    if not bucket or not key:
        return None
    return bucket, key


def _client(cfg: R2StorageConfig):
    import boto3  # lazy import
    from botocore.config import Config as BotoConfig

    endpoint = str(getattr(cfg, "endpoint", "") or "").strip()
    if not endpoint or endpoint.lower() in {"none", "null", "nil", "undefined"}:
        raise RuntimeError("R2 endpoint is not configured. Check R2_ENDPOINT in your environment.")
    if not endpoint.startswith(("http://", "https://")):
        raise RuntimeError(f"R2 endpoint is invalid: {endpoint}")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name=cfg.region,
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 4, "mode": "standard"},
            s3={"addressing_style": "path"},
        ),
    )


def upload_file(cfg: R2StorageConfig, *, key: str, file_path: Path, content_type: str = "application/octet-stream") -> str:
    c = _client(cfg)
    object_key = key.lstrip("/")
    with open(file_path, "rb") as fp:
        c.put_object(
            Bucket=cfg.bucket,
            Key=object_key,
            Body=fp,
            ContentType=content_type,
            ContentLength=int(file_path.stat().st_size),
        )
    return r2_uri(cfg.bucket, object_key)


def download_to_file(cfg: R2StorageConfig, *, bucket: str, key: str, dest_path: Path) -> None:
    c = _client(cfg)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    resp = c.get_object(Bucket=bucket, Key=key.lstrip("/"))
    body = resp["Body"]
    try:
        with open(dest_path, "wb") as out:
            while True:
                block = body.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
    finally:
        try:
            body.close()
        except Exception:
            pass


def head_object(cfg: R2StorageConfig, *, key: str) -> Dict[str, Any]:
    """HEAD 对象元数据（预签名 PUT 完成后校验大小）。"""
    c = _client(cfg)
    object_key = key.lstrip("/")
    resp = c.head_object(Bucket=cfg.bucket, Key=object_key)
    return {
        "content_length": int(resp.get("ContentLength") or 0),
        "content_type": str(resp.get("ContentType") or ""),
    }


def delete_object(cfg: R2StorageConfig, *, bucket: str, key: str) -> None:
    c = _client(cfg)
    c.delete_object(Bucket=bucket, Key=key.lstrip("/"))


def generate_presigned_put_url(
    cfg: R2StorageConfig,
    *,
    key: str,
    content_type: str = "application/octet-stream",
    expires_in: int = 3600,
) -> str:
    """客户端直传 PUT 的预签名 URL（S3/R2 兼容）。"""
    c = _client(cfg)
    object_key = key.lstrip("/")
    return c.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": cfg.bucket,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=int(expires_in),
    )

