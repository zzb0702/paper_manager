# -*- coding: utf-8 -*-
"""Tiny OpenAI-compatible chat client, used for ingest-time summaries."""

from __future__ import annotations

import json
import os

import requests

from .config import ROOT  # noqa: F401  (import side effect: loads .env)
from .convert import UA_HEADERS


def _parse_json_loose(body: str) -> dict:
    """Some gateways append an SSE tail ('data: [DONE]') after the JSON."""
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        end = body.rfind("}")
        if end > 0:
            return json.loads(body[: end + 1])
        raise


def chat(prompt: str, *, system: str = "", max_tokens: int = 400) -> str:
    base = os.getenv("LLM_BASE_URL", "").strip().rstrip("/")
    key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "").strip()
    if not (base and key and model):
        raise RuntimeError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 未配置")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = requests.post(
        f"{base}/chat/completions",
        headers={
            **UA_HEADERS,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=120,
    )
    resp.raise_for_status()
    # gateways omit charset; decode from bytes so UTF-8 Chinese survives
    choices = _parse_json_loose(resp.content.decode("utf-8", "replace")).get(
        "choices", []
    )
    if not choices:
        raise RuntimeError("LLM 响应缺少 choices")
    return (choices[0].get("message", {}) or {}).get("content", "").strip()


SUMMARY_PROMPT = (
    "以下是一篇论文的开头部分（标题/摘要/引言）。"
    "用中文写 3-5 句话总结它：研究什么问题、用了什么方法、关键结果或贡献。"
    "不要客套话，直接给信息，供后续检索使用。\n\n"
)


def summarize_paper(front_text: str) -> str | None:
    try:
        text = SUMMARY_PROMPT + front_text[:3500]
        out = chat(text, max_tokens=350)
        return out or None
    except Exception as exc:
        print(f"  [摘要跳过] {type(exc).__name__}: {str(exc)[:200]}")
        return None
