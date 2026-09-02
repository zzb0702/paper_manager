# -*- coding: utf-8 -*-
"""Tiny OpenAI-compatible chat client, used for ingest-time summaries."""

from __future__ import annotations

import json
import os

import requests

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


def chat(prompt: str, *, system: str = "", max_tokens: int = 400,
         timeout: float = 120) -> str:
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
        timeout=timeout,
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
        from .util import log

        log(f"  [摘要跳过] {type(exc).__name__}: {str(exc)[:200]}")
        return None


REWRITE_PROMPT = (
    "你是学术论文检索助手。把用户的问题改写成 2-3 组论文检索关键词，"
    "覆盖同义表述和英文对应说法（学术论文多为英文）。"
    "只输出一个 JSON 字符串数组，不要任何解释。\n"
    "示例：输出 [\"scaled dot-product attention\", \"self-attention efficiency\", \"注意力机制 并行\"]\n\n"
    "用户问题："
)


def rewrite_query(question: str) -> list[str] | None:
    """Expand a question into extra search-keyword variants (bilingual).

    Returns None when the LLM is unavailable or the reply is unparseable —
    callers then search with the original query only.
    """
    try:
        out = chat(REWRITE_PROMPT + question.strip(), max_tokens=150)
        start, end = out.find("["), out.rfind("]")
        if start < 0 or end <= start:
            return None
        arr = json.loads(out[start : end + 1])
        variants = [str(x).strip() for x in arr if str(x).strip()][:3]
        return variants or None
    except Exception:
        return None
