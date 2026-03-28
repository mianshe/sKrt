#!/usr/bin/env python3
"""
Start Alibaba Cloud ECS (GPU) instances via API.

Requires: pip install -r scripts/gpu_autostart/requirements.txt

Env:
  GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_ID
  GPU_AUTOSTART_ALIYUN_ACCESS_KEY_SECRET / ALIBABA_CLOUD_ACCESS_KEY_SECRET
  GPU_AUTOSTART_ALIYUN_REGION (e.g. cn-hongkong)
  GPU_AUTOSTART_ALIYUN_INSTANCE_IDS (comma-separated i-xxxxx)

RAM: allow ecs:StartInstance on target instances (DescribeInstances optional).
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
    parser = argparse.ArgumentParser(description="Aliyun ECS StartInstance (GPU autostart)")
    parser.add_argument("--dry-run", action="store_true", help="Print IDs and exit without API")
    parser.add_argument("--region", default="", help="Override GPU_AUTOSTART_ALIYUN_REGION")
    parser.add_argument(
        "--instance-ids",
        default="",
        help="Override GPU_AUTOSTART_ALIYUN_INSTANCE_IDS (comma-separated)",
    )
    args = parser.parse_args()
    _load_dotenv()

    ak = _env(
        "GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID",
        "ALIBABA_CLOUD_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_ID",
    )
    sk = _env(
        "GPU_AUTOSTART_ALIYUN_ACCESS_KEY_SECRET",
        "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
        "ALIYUN_ACCESS_KEY_SECRET",
    )
    region = (args.region or _env("GPU_AUTOSTART_ALIYUN_REGION", "ALIYUN_REGION", "ECS_REGION")).strip()
    ids_raw = (args.instance_ids or _env("GPU_AUTOSTART_ALIYUN_INSTANCE_IDS", "ALIYUN_INSTANCE_IDS")).strip()
    instance_ids = _parse_instance_ids(ids_raw)

    if not instance_ids:
        print("error: no instance IDs (set GPU_AUTOSTART_ALIYUN_INSTANCE_IDS)", file=sys.stderr)
        return 2
    if not region:
        print("error: missing region (GPU_AUTOSTART_ALIYUN_REGION)", file=sys.stderr)
        return 2

    print(f"region={region} instances={instance_ids}")
    if args.dry_run:
        print("dry-run: skipping StartInstance")
        return 0

    if not ak or not sk:
        print(
            "error: missing access key (GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID / ..._ACCESS_KEY_SECRET)",
            file=sys.stderr,
        )
        return 2

    try:
        from alibabacloud_ecs20140526.client import Client as EcsClient  # type: ignore
        from alibabacloud_ecs20140526 import models as ecs_models  # type: ignore
        from alibabacloud_tea_openapi import models as open_api_models  # type: ignore
    except ImportError:
        print(
            "error: install Aliyun SDK: pip install -r scripts/gpu_autostart/requirements.txt",
            file=sys.stderr,
        )
        return 3

    cfg = open_api_models.Config(access_key_id=ak, access_key_secret=sk)
    cfg.endpoint = f"ecs.{region}.aliyuncs.com"
    client = EcsClient(cfg)
    req = ecs_models.StartInstancesRequest(region_id=region, instance_id=instance_ids)
    resp = client.start_instances(req)
    body = getattr(resp, "body", resp)
    print("StartInstances ok:", body)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
