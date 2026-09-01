# -*- coding: utf-8 -*-
"""Hybrid retrieval: FTS5 + vector cosine -> RRF -> optional reranker.

Returns token-efficient, paper-deduplicated hits: a summary-level card
plus one best matching chunk snippet with section/page citation.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

import numpy as np

from . import db
from .embedder import EmbeddingClient, RerankerClient

RRF_K = 60
CAND_POOL = 20
SNIPPET_CHARS = 400


def _cosine(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    qnorm = np.linalg.norm(query) or 1.0
    mnorms = np.linalg.norm(matrix, axis=1)
    mnorms[mnorms == 0] = 1.0
    return (matrix @ query) / (mnorms * qnorm)


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 5,
    *,
    per_paper: bool = True,
    embedder: EmbeddingClient | None = None,
    reranker: RerankerClient | None = None,
) -> list[dict[str, Any]]:
    """Search the library. Never raises on embedding/rerank failure —
    silently degrades to FTS5 only."""
    fts_ids = db.search_fts(conn, query, k=30)

    vec_ids: list[int] = []
    if embedder is not None:
        try:
            qvec = embedder.embed([query])[0]
            table = db.load_vectors(conn)
            if table:
                ids = list(table.keys())
                matrix = np.array([table[i] for i in ids], dtype=np.float32)
                scores = _cosine(matrix, np.array(qvec, dtype=np.float32))
                order = np.argsort(-scores)[:30]
                vec_ids = [ids[i] for i in order]
        except Exception:
            vec_ids = []

    # reciprocal rank fusion
    fused: dict[int, float] = {}
    for rank, cid in enumerate(fts_ids):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, cid in enumerate(vec_ids):
        fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
    if not fused:
        return []

    pool = sorted(fused.items(), key=lambda kv: -kv[1])[:CAND_POOL]
    pool_ids = [cid for cid, _ in pool]
    rows = db.chunks_by_ids(conn, pool_ids)
    texts = [r["text"][:1500] for r in rows]

    if reranker is not None and len(rows) > 1:
        try:
            ranked = reranker.rerank(
                query=query, documents=texts, top_n=min(len(rows), max(top_k * 2, 8))
            )
            if ranked:
                order = {item["index"]: item["score"] for item in ranked}
                rows = sorted(
                    enumerate(rows), key=lambda kv: -order.get(kv[0], 0.0)
                )
                rows = [r for _, r in rows]
        except Exception:
            pass  # keep RRF order

    seen: set[int] = set()
    results: list[dict[str, Any]] = []
    for r in rows:
        pid = r["paper_id"]
        if per_paper and pid in seen:
            continue
        seen.add(pid)
        pages = ""
        if r["page_start"] is not None:
            pages = (
                str(r["page_start"])
                if r["page_start"] == r["page_end"]
                else f"{r['page_start']}-{r['page_end']}"
            )
        results.append(
            {
                "paper_id": pid,
                "title": r["title"],
                "year": r["year"],
                "summary": (r["paper_summary"] or "")[:500],
                "chunk_id": r["id"],
                "section": r["section"],
                "pages": pages,
                "score": round(float(fused.get(r["id"], 0.0)), 5),
                "snippet": r["text"][:SNIPPET_CHARS],
            }
        )
        if len(results) >= top_k:
            break
    return results


def format_hits(hits: list[dict[str, Any]]) -> str:
    """Compact markdown rendering for agents / CLI."""
    if not hits:
        return "没有找到相关论文。"
    lines = [f"找到 {len(hits)} 篇相关论文：", ""]
    for h in hits:
        year = f" ({h['year']})" if h["year"] else ""
        lines.append(f"## [{h['paper_id']}] {h['title']}{year}")
        if h["summary"]:
            lines.append(f"摘要卡：{h['summary']}")
        cite = h["section"] + (f"，第 {h['pages']} 页" if h["pages"] else "")
        lines.append(f"匹配位置：{cite}（chunk {h['chunk_id']}，RRF {h['score']}）")
        lines.append(f"片段：{h['snippet']}…")
        lines.append("")
    lines.append("用 read_paper_section(paper_id, section) 深入阅读某个章节。")
    return "\n".join(lines)
