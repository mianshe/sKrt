#!/usr/bin/env python3
"""
Start Tencent Cloud CVM (GPU) instances via API.

Requires: pip install -r scripts/gpu_autostart/requirements.txt

Env (GPU_AUTOSTART_* or fallback TENCENT_*):
  GPU_AUTOSTART_TENCENT_SECRET_ID / TENCENTCLOUD_SECRET_ID
  GPU_AUTOSTART_TENCENT_SECRET_KEY / TENCENTCLOUD_SECRET_KEY
  GPU_AUTOSTART_TENCENT_REGION (e.g. ap-hongkong)
  GPU_AUTOSTART_TENCENT_INSTANCE_IDS (comma-separated ins-xxx)

IAM: allow cvm:StartInstances on target instances (DescribeInstances optional for status checks).
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    here = os.path.dirname(os.path.abspath(__file__))
    for root in (
        os.path.join(here, "..", ".."),
        here,
        os.getcwd(),
    ):
        env_path = os.path.abspath(os.path.join(root, ".env"))
        if os.path.isfile(env_path):
            load_dotenv(env_path)
            return


def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return default


def _parse_instance_ids(raw: str) -> List[str]:
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p]


def main() -> int:
    parser = argparse.ArgumentParser(description="Tencent CVM StartInstances (GPU autostart)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print instance IDs and exit without calling API",
    )
    parser.add_argument(
        "--region",
        default="",
        help="Override GPU_AUTOSTART_TENCENT_REGION",
    )
    parser.add_argument(
        "--instance-ids",
        default="",
        help="Override GPU_AUTOSTART_TENCENT_INSTANCE_IDS (comma-separated)",
    )
    args = parser.parse_args()
    _load_dotenv()

    secret_id = _env("GPU_AUTOSTART_TENCENT_SECRET_ID", "TENCENTCLOUD_SECRET_ID", "TENCENT_SECRET_ID")
    secret_key = _env("GPU_AUTOSTART_TENCENT_SECRET_KEY", "TENCENTCLOUD_SECRET_KEY", "TENCENT_SECRET_KEY")
    region = (args.region or _env("GPU_AUTOSTART_TENCENT_REGION", "TENCENT_REGION")).strip()
    ids_raw = (args.instance_ids or _env("GPU_AUTOSTART_TENCENT_INSTANCE_IDS", "TENCENT_INSTANCE_IDS")).strip()
    instance_ids = _parse_instance_ids(ids_raw)

    if not instance_ids:
        print("error: no instance IDs (set GPU_AUTOSTART_TENCENT_INSTANCE_IDS)", file=sys.stderr)
        return 2
    if not region:
        print("error: missing region (GPU_AUTOSTART_TENCENT_REGION)", file=sys.stderr)
        return 2

    print(f"region={region} instances={instance_ids}")
    if args.dry_run:
        print("dry-run: skipping StartInstances")
        return 0

    if not secret_id or not secret_key:
        print("error: missing secret id/key (GPU_AUTOSTART_TENCENT_SECRET_ID / ..._SECRET_KEY)", file=sys.stderr)
        return 2

    try:
        from tencentcloud.common import credential  # type: ignore
        from tencentcloud.common.profile.client_profile import ClientProfile  # type: ignore
        from tencentcloud.common.profile.http_profile import HttpProfile  # type: ignore
        from tencentcloud.cvm.v20170312 import cvm_client, models  # type: ignore
    except ImportError:
        print(
            "error: install Tencent SDK: pip install -r scripts/gpu_autostart/requirements.txt",
            file=sys.stderr,
        )
        return 3

    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = "cvm.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = cvm_client.CvmClient(cred, region, client_profile)
    req = models.StartInstancesRequest()
    req.InstanceIds = instance_ids
    resp = client.StartInstances(req)
    print("StartInstances ok:", resp.to_json_string() if hasattr(resp, "to_json_string") else resp)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
