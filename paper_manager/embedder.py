# -*- coding: utf-8 -*-
"""Embedding / reranker adapters (shared env contract, see config.py).

EMBEDDING_BASE_URL + EMBEDDING_MODEL enable vectors;
RERANK_BASE_URL + RERANK_MODEL enable reranking;
either key falls back to SILICONFLOW_API_KEY then LLM_API_KEY.
"""

from __future__ import annotations

import os
from typing import Any

import requests


def _endpoint(base_url: str, suffix: str) -> str:
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith(suffix) else base_url + suffix


class EmbeddingClient:
    def __init__(self, *, base_url: str, model: str, api_key: str | None = None):
        self.url = _endpoint(base_url, "/embeddings")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = float(os.getenv("EMBEDDING_TIMEOUT", "60"))

    @classmethod
    def from_env(cls) -> "EmbeddingClient | None":
        base_url = os.getenv("EMBEDDING_BASE_URL", "").strip()
        model = os.getenv("EMBEDDING_MODEL", "").strip()
        if not base_url or not model:
            return None
        api_key = (
            os.getenv("EMBEDDING_API_KEY", "").strip()
            or os.getenv("SILICONFLOW_API_KEY", "").strip()
            or os.getenv("LLM_API_KEY", "").strip()
        )
        return cls(base_url=base_url, model=model, api_key=api_key or None)

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = requests.post(
            self.url,
            headers=headers,
            json={"model": self.model, "input": texts},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        data = sorted(data, key=lambda item: int(item.get("index", 0)))
        vectors = [item.get("embedding") for item in data]
        if len(vectors) != len(texts) or any(not isinstance(v, list) for v in vectors):
            raise ValueError("Embedding 响应数量或格式不正确")
        return vectors


class RerankerClient:
    def __init__(self, *, base_url: str, model: str, api_key: str | None = None):
        self.url = _endpoint(base_url, "/rerank")
        self.model = model
        self.api_key = api_key or ""
        self.timeout = float(os.getenv("RERANK_TIMEOUT", "60"))

    @classmethod
    def from_env(cls) -> "RerankerClient | None":
        base_url = os.getenv("RERANK_BASE_URL", "").strip()
        model = os.getenv("RERANK_MODEL", "").strip()
        if not base_url or not model:
            return None
        api_key = (
            os.getenv("RERANK_API_KEY", "").strip()
            or os.getenv("SILICONFLOW_API_KEY", "").strip()
            or os.getenv("LLM_API_KEY", "").strip()
        )
        return cls(base_url=base_url, model=model, api_key=api_key or None)

    def rerank(
        self, *, query: str, documents: list[str], top_n: int
    ) -> list[dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = requests.post(
            self.url,
            headers=headers,
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        normalized: list[dict[str, Any]] = []
        for pos, item in enumerate(results):
            try:
                normalized.append(
                    {
                        "index": int(item.get("index", pos)),
                        "score": float(
                            item.get("relevance_score", item.get("score", 0.0))
                        ),
                    }
                )
            except (TypeError, ValueError):
                continue
        return sorted(normalized, key=lambda x: x["score"], reverse=True)[:top_n]
