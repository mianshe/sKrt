"""Call Tencent / Aliyun APIs to start or stop GPU ECS instances."""
from __future__ import annotations
import os
from typing import Any, Dict, List

def _env(*names: str, default: str = "") -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return default

def _parse_instance_ids(raw: str) -> List[str]:
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    return [p for p in parts if p]

def gpu_autostart_enabled() -> bool:
    return (os.getenv("GPU_AUTOSTART_ENABLED") or "0").strip().lower() in {"1", "true", "yes", "on"}

def _provider() -> str:
    return (os.getenv("GPU_AUTOSTART_PROVIDER") or "tencent").strip().lower()

def _tencent_start(instance_ids: List[str], region: str) -> str:
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.cvm.v20170312 import cvm_client, models
    secret_id = _env("GPU_AUTOSTART_TENCENT_SECRET_ID", "TENCENTCLOUD_SECRET_ID", "TENCENT_SECRET_ID")
    secret_key = _env("GPU_AUTOSTART_TENCENT_SECRET_KEY", "TENCENTCLOUD_SECRET_KEY", "TENCENT_SECRET_KEY")
    if not secret_id or not secret_key:
        raise RuntimeError("missing GPU_AUTOSTART_TENCENT_SECRET_ID / SECRET_KEY")
    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = "cvm.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = cvm_client.CvmClient(cred, region, client_profile)
    req = models.StartInstancesRequest()
    req.InstanceIds = instance_ids
    resp = client.StartInstances(req)
    return resp.to_json_string() if hasattr(resp, "to_json_string") else str(resp)

def _tencent_stop(instance_ids: List[str], region: str) -> str:
    from tencentcloud.common import credential
    from tencentcloud.common.profile.client_profile import ClientProfile
    from tencentcloud.common.profile.http_profile import HttpProfile
    from tencentcloud.cvm.v20170312 import cvm_client, models
    secret_id = _env("GPU_AUTOSTART_TENCENT_SECRET_ID", "TENCENTCLOUD_SECRET_ID", "TENCENT_SECRET_ID")
    secret_key = _env("GPU_AUTOSTART_TENCENT_SECRET_KEY", "TENCENTCLOUD_SECRET_KEY", "TENCENT_SECRET_KEY")
    if not secret_id or not secret_key:
        raise RuntimeError("missing GPU_AUTOSTART_TENCENT_SECRET_ID / SECRET_KEY")
    cred = credential.Credential(secret_id, secret_key)
    http_profile = HttpProfile()
    http_profile.endpoint = "cvm.tencentcloudapi.com"
    client_profile = ClientProfile()
    client_profile.httpProfile = http_profile
    client = cvm_client.CvmClient(cred, region, client_profile)
    req = models.StopInstancesRequest()
    req.InstanceIds = instance_ids
    resp = client.StopInstances(req)
    return resp.to_json_string() if hasattr(resp, "to_json_string") else str(resp)

def _aliyun_start(instance_ids: List[str], region: str) -> str:
    from alibabacloud_ecs20140526.client import Client as EcsClient
    from alibabacloud_ecs20140526 import models as ecs_models
    from alibabacloud_tea_openapi import models as open_api_models
    ak = _env("GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_ID")
    sk = _env("GPU_AUTOSTART_ALIYUN_ACCESS_KEY_SECRET", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIYUN_ACCESS_KEY_SECRET")
    if not ak or not sk:
        raise RuntimeError("missing GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID / ACCESS_KEY_SECRET")
    cfg = open_api_models.Config(access_key_id=ak, access_key_secret=sk)
    cfg.endpoint = f"ecs.{region}.aliyuncs.com"
    client = EcsClient(cfg)
    req = ecs_models.StartInstancesRequest(region_id=region, instance_id=instance_ids)
    resp = client.start_instances(req)
    return str(getattr(resp, "body", resp))

def _aliyun_stop(instance_ids: List[str], region: str) -> str:
    from alibabacloud_ecs20140526.client import Client as EcsClient
    from alibabacloud_ecs20140526 import models as ecs_models
    from alibabacloud_tea_openapi import models as open_api_models
    ak = _env("GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_ID")
    sk = _env("GPU_AUTOSTART_ALIYUN_ACCESS_KEY_SECRET", "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIYUN_ACCESS_KEY_SECRET")
    if not ak or not sk:
        raise RuntimeError("missing GPU_AUTOSTART_ALIYUN_ACCESS_KEY_ID / ACCESS_KEY_SECRET")
    cfg = open_api_models.Config(access_key_id=ak, access_key_secret=sk)
    cfg.endpoint = f"ecs.{region}.aliyuncs.com"
    client = EcsClient(cfg)
    req = ecs_models.StopInstancesRequest(region_id=region, instance_id=instance_ids)
    resp = client.stop_instances(req)
    return str(getattr(resp, "body", resp))

def start_gpu_instances() -> Dict[str, Any]:
    if not gpu_autostart_enabled():
        raise RuntimeError("GPU_AUTOSTART_ENABLED is off")
    prov = _provider()
    if prov == "tencent":
        region = _env("GPU_AUTOSTART_TENCENT_REGION", "TENCENT_REGION").strip()
        ids = _parse_instance_ids(_env("GPU_AUTOSTART_TENCENT_INSTANCE_IDS", "TENCENT_INSTANCE_IDS"))
        if not ids or not region:
            raise RuntimeError("missing TENCENT region or instance IDs")
        raw = _tencent_start(ids, region)
        return {"provider": "tencent", "region": region, "instance_ids": ids, "response": raw}
    if prov == "aliyun":
        region = _env("GPU_AUTOSTART_ALIYUN_REGION", "ALIYUN_REGION", "ECS_REGION").strip()
        ids = _parse_instance_ids(_env("GPU_AUTOSTART_ALIYUN_INSTANCE_IDS", "ALIYUN_INSTANCE_IDS"))
        if not ids or not region:
            raise RuntimeError("missing ALIYUN region or instance IDs")
        raw = _aliyun_start(ids, region)
        return {"provider": "aliyun", "region": region, "instance_ids": ids, "response": raw}
    raise RuntimeError(f"unknown GPU_AUTOSTART_PROVIDER: {prov}")

def stop_gpu_instances() -> Dict[str, Any]:
    if not gpu_autostart_enabled():
        raise RuntimeError("GPU_AUTOSTART_ENABLED is off")
    prov = _provider()
    if prov == "tencent":
        region = _env("GPU_AUTOSTART_TENCENT_REGION", "TENCENT_REGION").strip()
        ids = _parse_instance_ids(_env("GPU_AUTOSTART_TENCENT_INSTANCE_IDS", "TENCENT_INSTANCE_IDS"))
        if not ids or not region:
            raise RuntimeError("missing TENCENT region or instance IDs")
        raw = _tencent_stop(ids, region)
        return {"provider": "tencent", "region": region, "instance_ids": ids, "response": raw}
    if prov == "aliyun":
        region = _env("GPU_AUTOSTART_ALIYUN_REGION", "ALIYUN_REGION", "ECS_REGION").strip()
        ids = _parse_instance_ids(_env("GPU_AUTOSTART_ALIYUN_INSTANCE_IDS", "ALIYUN_INSTANCE_IDS"))
        if not ids or not region:
            raise RuntimeError("missing ALIYUN region or instance IDs")
        raw = _aliyun_stop(ids, region)
        return {"provider": "aliyun", "region": region, "instance_ids": ids, "response": raw}
    raise RuntimeError(f"unknown GPU_AUTOSTART_PROVIDER: {prov}")
