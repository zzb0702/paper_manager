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
    "必须同时覆盖：中文表述、对应英文术语（论文几乎都是英文）、常见缩写。"
    "只输出一个 JSON 字符串数组，不要任何解释。\n"
    "示例：用户「图检索增强生成」→ "
    "[\"GraphRAG\", \"graph retrieval augmented generation\", \"图检索 增强生成\"]\n\n"
    "用户问题："
)

# Offline bilingual glossary — always merged into query variants so search
# still expands when the LLM gateway is down. Order matters: more specific
# entries first so GraphRAG outranks generic RAG when both match.
_TERM_GLOSSARY: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("图检索增强", "图检索增强生成", "图增强检索", "知识图谱检索"), ("GraphRAG", "graph retrieval augmented generation")),
    (("轻量图检索",), ("LightRAG", "light rag")),
    (("自我反思检索", "自反思检索"), ("Self-RAG", "self-reflective retrieval")),
    (("纠正式检索", "纠错检索"), ("Corrective RAG", "CRAG")),
    (("假设文档嵌入",), ("HyDE", "hypothetical document embeddings")),
    (("检索增强", "检索增强生成", "增强检索"), ("retrieval-augmented generation", "RAG")),
    (("图检索",), ("graph retrieval", "GraphRAG")),
    (("注意力机制",), ("attention", "self-attention")),
    (("向量数据库",), ("vector database", "vector search")),
    (("重排序", "重排"), ("rerank", "reranker")),
    (("知识图谱",), ("knowledge graph", "KG")),
    (("大语言模型",), ("large language model", "LLM")),
    (("零样本",), ("zero-shot",)),
    (("少样本",), ("few-shot",)),
    (("提示工程",), ("prompt engineering",)),
    (("微调",), ("fine-tuning", "finetune")),
    (("基准测试",), ("benchmark",)),
    (("消融实验",), ("ablation",)),
]


def expand_query_offline(query: str) -> list[str]:
    """Deterministic CN↔EN term expansion (no network)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    # Prefer longer/more specific CN anchors first so 图检索增强生成
    # hits GraphRAG before the generic RAG entry. Stable-sort keeps
    # glossary order for equal-length anchors.
    entries = sorted(
        enumerate(_TERM_GLOSSARY),
        key=lambda pair: (
            -max((len(c) for c in pair[1][0]), default=0),
            pair[0],
        ),
    )
    out: list[str] = []
    for _idx, (cns, ens) in entries:
        hit = any(cn in q for cn in cns) or any(en.lower() in q for en in ens)
        if not hit:
            continue
        for term in ens:
            if term.lower() not in q and term not in out:
                out.append(term)
        for term in cns:
            if term not in query and term not in out:
                out.append(term)
    return out[:4]


def rewrite_query(question: str) -> list[str] | None:
    """Expand a question into extra search-keyword variants (bilingual).

    Always merges offline glossary hits; LLM variants (if any) come first.
    Returns None when nothing extra was produced.
    """
    variants: list[str] = []
    try:
        out = chat(REWRITE_PROMPT + question.strip(), max_tokens=150)
        start, end = out.find("["), out.rfind("]")
        if start >= 0 and end > start:
            arr = json.loads(out[start : end + 1])
            for x in arr:
                s = str(x).strip()
                if s and s not in variants:
                    variants.append(s)
            variants = variants[:3]
    except Exception:
        pass

    for term in expand_query_offline(question):
        if term not in variants:
            variants.append(term)
    variants = variants[:4]
    return variants or None
