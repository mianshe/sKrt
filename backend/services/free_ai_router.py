import asyncio
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from backend.runtime_config import HybridModelConfig, RuntimeConfig


class FreeAIRouter:
    def __init__(self, runtime_config: Optional[RuntimeConfig] = None) -> None:
        self.runtime_config = runtime_config or RuntimeConfig.from_env()
        self.hybrid_cfg: HybridModelConfig = self.runtime_config.hybrid

        self.github_token = os.getenv("GITHUB_TOKEN", "").strip()
        self.zhipu_token = os.getenv("ZHIPU_API_KEY", "").strip()
        self.hf_token = os.getenv("HF_TOKEN", "").strip()
        self.github_base = os.getenv("GITHUB_MODELS_BASE_URL", "https://models.inference.ai.azure.com").rstrip("/")
        self.github_embed_model = os.getenv("GITHUB_EMBED_MODEL", "text-embedding-3-small").strip() or "text-embedding-3-small"
        self.zhipu_base = os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
        self.zhipu_chat_model = os.getenv("ZHIPU_CHAT_MODEL", "glm-4-flash").strip() or "glm-4-flash"
        self.zhipu_embed_model = os.getenv("ZHIPU_EMBED_MODEL", "embedding-3").strip() or "embedding-3"
        self.hf_base = os.getenv("HF_INFERENCE_BASE_URL", "https://api-inference.huggingface.co/models").rstrip("/")
        self.hf_chat_model = os.getenv("HF_CHAT_MODEL", "microsoft/Phi-3.5-mini-instruct").strip()
        self.hf_embed_model = os.getenv("HF_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2").strip()

        self._local_chat_runtime: Optional[Tuple[Any, Any]] = None
        self._local_embed_runtime: Optional[Tuple[Any, Any, Any]] = None

    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o-mini",
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        return await self.chat_with_task(
            messages=messages,
            task_type="general",
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            prefer_free=False,
        )

    def _chat_provider_order(self, prefer_free: bool) -> List[str]:
        """顺序受 HYBRID_LOCAL_FIRST 控制：本地优先或远程 API 优先，本地 transformers 作备用。"""
        remotes: List[str] = []
        if self.hybrid_cfg.enable_remote_fallback:
            if prefer_free:
                remotes = ["github-models", "huggingface", "zhipu"]
            else:
                remotes = ["github-models", "zhipu", "huggingface"]
        local = "transformers-local"
        if self.hybrid_cfg.local_first:
            order: List[str] = []
            if self.hybrid_cfg.enable_local_chat:
                order.append(local)
            order.extend(remotes)
            return order
        order = list(remotes)
        if self.hybrid_cfg.enable_local_chat:
            order.append(local)
        return order

    async def chat_with_task(
        self,
        messages: List[Dict[str, str]],
        task_type: str = "general",
        model: str = "gpt-4o-mini",
        max_tokens: int = 700,
        temperature: float = 0.2,
        prefer_free: bool = True,
    ) -> Dict[str, Any]:
        provider_order = self._chat_provider_order(prefer_free=prefer_free)
        return await self._chat_with_provider_order(
            messages=messages,
            provider_order=provider_order,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            task_type=task_type,
        )

    async def _chat_with_provider_order(
        self,
        messages: List[Dict[str, str]],
        provider_order: List[str],
        model: str,
        max_tokens: int,
        temperature: float,
        task_type: str,
    ) -> Dict[str, Any]:
        for provider in provider_order:
            if provider == "transformers-local":
                if self.hybrid_cfg.enable_local_chat:
                    local_resp = await self._local_chat(messages, max_tokens=max_tokens, temperature=temperature)
                    if local_resp is not None:
                        return {"provider": "transformers-local", "content": local_resp, "task_type": task_type}
                continue
            if not self.hybrid_cfg.enable_remote_fallback:
                continue
            if provider == "github-models":
                github_resp = await self._github_chat(messages, model, max_tokens, temperature)
                if github_resp is not None:
                    return {"provider": "github-models", "content": github_resp, "task_type": task_type}
                continue
            if provider == "zhipu":
                zhipu_resp = await self._zhipu_chat(messages, max_tokens, temperature)
                if zhipu_resp is not None:
                    return {"provider": "zhipu", "content": zhipu_resp, "task_type": task_type}
                continue
            if provider == "huggingface":
                hf_resp = await self._hf_chat(messages, max_tokens, temperature)
                if hf_resp is not None:
                    return {"provider": "huggingface", "content": hf_resp, "task_type": task_type}
                continue

        if self.hybrid_cfg.enable_hash_fallback:
            return {"provider": "hash-fallback", "content": self._hash_chat(messages), "task_type": task_type}
        return {"provider": "none", "content": "", "task_type": task_type}

    async def embed(
        self, text: str, model: str = "text-embedding-3-small", dimensions: Optional[int] = None
    ) -> Dict[str, Any]:
        target_dim = dimensions or self.hybrid_cfg.embedding_dimensions

        if self.hybrid_cfg.local_first and self.hybrid_cfg.enable_local_embedding:
            local_vec = await self._local_embedding(text)
            if local_vec:
                local_dim = len(local_vec)
                final_dim = dimensions or local_dim or target_dim
                return {
                    "provider": "transformers-local",
                    "embedding": self._normalize_vector_dimensions(local_vec, final_dim),
                    "model_id": self.hybrid_cfg.local_embedding_model_id,
                    "billable_tokens": 0,
                }

        if self.hybrid_cfg.enable_remote_fallback:
            github_vec = await self._github_embedding(text, model)
            if github_vec:
                return {
                    "provider": "github-models",
                    "embedding": self._normalize_vector_dimensions(github_vec, target_dim),
                    "model_id": model,
                    "billable_tokens": 0,
                }

            zhipu_resp = await self._zhipu_embedding(text)
            if zhipu_resp:
                return {
                    "provider": "zhipu",
                    "embedding": self._normalize_vector_dimensions(zhipu_resp.get("embedding", []), target_dim),
                    "model_id": self.zhipu_embed_model,
                    "billable_tokens": int(zhipu_resp.get("billable_tokens") or 0),
                    "usage": zhipu_resp.get("usage") or {},
                }

            hf_vec = await self._hf_embedding(text)
            if hf_vec:
                return {
                    "provider": "huggingface",
                    "embedding": self._normalize_vector_dimensions(hf_vec, target_dim),
                    "model_id": self.hf_embed_model,
                    "billable_tokens": 0,
                }

        if not self.hybrid_cfg.local_first and self.hybrid_cfg.enable_local_embedding:
            local_vec = await self._local_embedding(text)
            if local_vec:
                local_dim = len(local_vec)
                final_dim = dimensions or local_dim or target_dim
                return {
                    "provider": "transformers-local",
                    "embedding": self._normalize_vector_dimensions(local_vec, final_dim),
                    "model_id": self.hybrid_cfg.local_embedding_model_id,
                    "billable_tokens": 0,
                }

        if self.hybrid_cfg.enable_hash_fallback:
            return {
                "provider": "hash-fallback",
                "embedding": self._hash_embedding(text, target_dim),
                "model_id": "hash-embedding",
                "billable_tokens": 0,
            }
        return {"provider": "none", "embedding": self._hash_embedding(text, target_dim), "model_id": "hash-embedding", "billable_tokens": 0}

    def _primary_remote_embed_model_id(self) -> Optional[str]:
        if not self.hybrid_cfg.enable_remote_fallback:
            return None
        if self.github_token:
            return self.github_embed_model
        if self.zhipu_token:
            return self.zhipu_embed_model
        if self.hf_token:
            return self.hf_embed_model
        return None

    def get_active_embedding_model_id(self) -> str:
        remote = self._primary_remote_embed_model_id()
        local_id = self.hybrid_cfg.local_embedding_model_id
        if self.hybrid_cfg.local_first:
            if self.hybrid_cfg.enable_local_embedding:
                return local_id
            if remote:
                return remote
        else:
            if remote:
                return remote
            if self.hybrid_cfg.enable_local_embedding:
                return local_id
        if self.hybrid_cfg.enable_local_embedding:
            return local_id
        if remote:
            return remote
        return "hash-embedding"

    async def _github_chat(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        if not self.github_token:
            return None
        url = f"{self.github_base}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.github_token}", "Content-Type": "application/json"}
        data = await self._post_json_with_retries(url, headers, payload, self.hybrid_cfg.remote_timeout_seconds)
        if not data:
            return None
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    async def _github_embedding(self, text: str, model: str) -> Optional[List[float]]:
        if not self.github_token:
            return None
        url = f"{self.github_base}/embeddings"
        payload = {"model": model, "input": text}
        headers = {"Authorization": f"Bearer {self.github_token}", "Content-Type": "application/json"}
        data = await self._post_json_with_retries(url, headers, payload, self.hybrid_cfg.remote_timeout_seconds)
        if not data:
            return None
        try:
            return data["data"][0]["embedding"]
        except Exception:
            return None

    async def _zhipu_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        if not self.zhipu_token:
            return None
        url = f"{self.zhipu_base}/chat/completions"
        payload = {
            "model": self.zhipu_chat_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {self.zhipu_token}", "Content-Type": "application/json"}
        data = await self._post_json_with_retries(url, headers, payload, self.hybrid_cfg.remote_timeout_seconds)
        if not data:
            return None
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    async def _zhipu_embedding(self, text: str) -> Optional[Dict[str, Any]]:
        if not self.zhipu_token:
            return None
        url = f"{self.zhipu_base}/embeddings"
        payload = {"model": self.zhipu_embed_model, "input": text}
        headers = {"Authorization": f"Bearer {self.zhipu_token}", "Content-Type": "application/json"}
        data = await self._post_json_with_retries(url, headers, payload, self.hybrid_cfg.remote_timeout_seconds)
        if not data:
            return None
        try:
            embedding = data.get("data", [{}])[0].get("embedding")
            if isinstance(embedding, list):
                usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
                billable_tokens = prompt_tokens if isinstance(prompt_tokens, int) else total_tokens if isinstance(total_tokens, int) else 0
                return {
                    "embedding": [float(x) for x in embedding],
                    "billable_tokens": max(0, int(billable_tokens or 0)),
                    "usage": usage if isinstance(usage, dict) else {},
                }
        except Exception:
            return None
        return None

    async def _hf_chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        if not self.hf_token:
            return None
        prompt = "\n".join([f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages])
        url = f"{self.hf_base}/{self.hf_chat_model}"
        payload = {
            "inputs": prompt,
            "parameters": {"max_new_tokens": max_tokens, "temperature": temperature, "return_full_text": False},
        }
        headers = {"Authorization": f"Bearer {self.hf_token}", "Content-Type": "application/json"}
        data = await self._post_json_with_retries(url, headers, payload, max(self.hybrid_cfg.remote_timeout_seconds, 35.0))
        if not data:
            return None
        if isinstance(data, list) and data and "generated_text" in data[0]:
            return data[0]["generated_text"]
        if isinstance(data, dict) and "generated_text" in data:
            return str(data["generated_text"])
        return None

    async def _hf_embedding(self, text: str) -> Optional[List[float]]:
        if not self.hf_token:
            return None
        url = f"{self.hf_base}/{self.hf_embed_model}"
        headers = {"Authorization": f"Bearer {self.hf_token}", "Content-Type": "application/json"}
        payload = {"inputs": text}
        data = await self._post_json_with_retries(url, headers, payload, max(self.hybrid_cfg.remote_timeout_seconds, 30.0))
        if not data:
            return None
        if isinstance(data, list) and data and isinstance(data[0], list):
            return [float(x) for x in data[0]]
        if isinstance(data, list) and data and isinstance(data[0], (int, float)):
            return [float(x) for x in data]
        return None

    async def _local_chat(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float) -> Optional[str]:
        try:
            return await asyncio.to_thread(self._local_chat_blocking, messages, max_tokens, temperature)
        except Exception:
            return None

    def _local_chat_blocking(self, messages: List[Dict[str, str]], max_tokens: int, temperature: float) -> Optional[str]:
        runtime = self._load_local_chat_runtime()
        if runtime is None:
            return None
        tokenizer, model = runtime

        prompt = self._flatten_messages(messages)
        inputs = tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
        model_inputs = {k: v.to(model.device) for k, v in inputs.items()}
        outputs = model.generate(
            **model_inputs,
            max_new_tokens=min(max_tokens, self.hybrid_cfg.local_chat_max_new_tokens),
            temperature=max(0.0, temperature if temperature is not None else self.hybrid_cfg.local_chat_temperature),
            do_sample=True,
            pad_token_id=tokenizer.eos_token_id,
        )
        input_len = model_inputs["input_ids"].shape[-1]
        generated = outputs[0][input_len:]
        text = tokenizer.decode(generated, skip_special_tokens=True).strip()
        return text or None

    async def _local_embedding(self, text: str) -> Optional[List[float]]:
        try:
            return await asyncio.to_thread(self._local_embedding_blocking, text)
        except Exception:
            return None

    def _local_embedding_blocking(self, text: str) -> Optional[List[float]]:
        runtime = self._load_local_embedding_runtime()
        if runtime is None:
            return None
        torch, tokenizer, model = runtime

        encoded = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        encoded = {k: v.to(model.device) for k, v in encoded.items()}
        with torch.no_grad():
            outputs = model(**encoded)
        last_hidden = outputs.last_hidden_state
        attention_mask = encoded["attention_mask"].unsqueeze(-1).expand(last_hidden.size()).float()
        summed = (last_hidden * attention_mask).sum(dim=1)
        counts = attention_mask.sum(dim=1).clamp(min=1e-9)
        embedding = (summed / counts)[0].tolist()
        if not isinstance(embedding, list) or not embedding:
            return None
        return [float(x) for x in embedding]

    def _load_local_chat_runtime(self) -> Optional[Tuple[Any, Any]]:
        if self._local_chat_runtime is not None:
            return self._local_chat_runtime
        try:
            import torch  # type: ignore
            from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

            tokenizer = AutoTokenizer.from_pretrained(self.hybrid_cfg.local_chat_model_id)
            model = AutoModelForCausalLM.from_pretrained(self.hybrid_cfg.local_chat_model_id)
            device = "cuda" if getattr(torch, "cuda", None) and torch.cuda.is_available() else "cpu"
            model = model.to(device)
            self._local_chat_runtime = (tokenizer, model)
        except Exception:
            self._local_chat_runtime = None
        return self._local_chat_runtime

    def _load_local_embedding_runtime(self) -> Optional[Tuple[Any, Any, Any]]:
        if self._local_embed_runtime is not None:
            return self._local_embed_runtime
        try:
            import torch  # type: ignore
            from transformers import AutoModel, AutoTokenizer  # type: ignore

            tokenizer = AutoTokenizer.from_pretrained(self.hybrid_cfg.local_embedding_model_id)
            model = AutoModel.from_pretrained(self.hybrid_cfg.local_embedding_model_id)
            device = "cuda" if getattr(torch, "cuda", None) and torch.cuda.is_available() else "cpu"
            model = model.to(device)
            self._local_embed_runtime = (torch, tokenizer, model)
        except Exception:
            self._local_embed_runtime = None
        return self._local_embed_runtime

    async def _post_json_with_retries(
        self, url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout_seconds: float
    ) -> Optional[Any]:
        attempts = max(1, self.hybrid_cfg.remote_max_retries + 1)
        delay = self.hybrid_cfg.remote_retry_backoff_seconds
        for idx in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    resp.raise_for_status()
                    return resp.json()
            except Exception:
                if idx >= attempts - 1:
                    return None
                if delay > 0:
                    await asyncio.sleep(delay * (2**idx))
        return None

    def _normalize_vector_dimensions(self, values: List[float], dimensions: int) -> List[float]:
        vec = [float(v) for v in values]
        if len(vec) >= dimensions:
            return vec[:dimensions]
        if not vec:
            return self._hash_embedding("empty-vector", dimensions)
        padded = vec[:]
        while len(padded) < dimensions:
            padded.append(0.0)
        return padded

    def _flatten_messages(self, messages: List[Dict[str, str]]) -> str:
        out: List[str] = []
        for m in messages:
            role = str(m.get("role", "user")).strip() or "user"
            content = str(m.get("content", "")).strip()
            out.append(f"{role}: {content}")
        return "\n".join(out).strip()

    def _hash_chat(self, messages: List[Dict[str, str]]) -> str:
        merged = " ".join([m.get("content", "") for m in messages]).strip()
        if not merged:
            merged = "用户未提供输入。"
        digest = hashlib.sha256(merged.encode("utf-8")).hexdigest()[:16]
        return (
            f"当前处于离线哈希应答模式（trace={digest}）。\n"
            "我无法访问外部模型，但系统已稳定降级。建议：\n"
            "1) 补充更具体的问题；2) 上传相关资料；3) 启用 GITHUB_TOKEN 或 ZHIPU_API_KEY 获得高质量回答。"
        )

    def _hash_embedding(self, text: str, dimensions: int = 256) -> List[float]:
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        vector: List[float] = []
        current = seed
        while len(vector) < dimensions:
            current = hashlib.sha256(current).digest()
            for b in current:
                vector.append((b / 255.0) * 2.0 - 1.0)
                if len(vector) >= dimensions:
                    break
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]

    @staticmethod
    def safe_json_loads(value: str, default: Any) -> Any:
        try:
            return json.loads(value)
        except Exception:
            return default
