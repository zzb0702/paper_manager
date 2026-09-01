# -*- coding: utf-8 -*-
"""Hybrid retrieval: FTS5 + vector cosine -> RRF -> optional reranker.

Returns token-efficient, paper-deduplicated hits: a summary-level card
plus one best matching chunk snippet with section/page citation.

Vector speed at scale: all chunk vectors are held in an in-process numpy
matrix keyed by a cheap (COUNT, MAX(rowid)) version stamp on chunk_vectors,
so a long-lived MCP server pays the full load once and reloads only after
an ingest (its own or another process's) changes the table.

Precision at scale: optional paper-level metadata filters (year range,
author, venue) are applied during candidate generation — FTS and vector
pools over-fetch, get filtered against the allowed paper set, then enter
RRF.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np

from . import db
from .embedder import EmbeddingClient, RerankerClient

RRF_K = 60
CAND_POOL = 20
FILTER_OVERFETCH = 200
SNIPPET_CHARS = 400


def _cosine(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    qnorm = np.linalg.norm(query) or 1.0
    mnorms = np.linalg.norm(matrix, axis=1)
    mnorms[mnorms == 0] = 1.0
    return (matrix @ query) / (mnorms * qnorm)


class _VectorIndex:
    """Process-wide cache of every chunk vector, aligned with chunk ids
    and their owning paper ids."""

    def __init__(self) -> None:
        self.version: tuple[int, int] | None = None
        self.ids = np.array([], dtype=np.int64)
        self.matrix = np.zeros((0, 1), dtype=np.float32)
        self.paper_of = np.array([], dtype=np.int64)

    @staticmethod
    def _stamp(conn: sqlite3.Connection) -> tuple[int, int]:
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(MAX(rowid), 0) m FROM chunk_vectors"
        ).fetchone()
        return (int(row["c"]), int(row["m"]))

    def get(
        self, conn: sqlite3.Connection
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        stamp = self._stamp(conn)
        if stamp != self.version:
            rows = conn.execute(
                "SELECT v.chunk_id cid, v.embedding emb, c.paper_id pid "
                "FROM chunk_vectors v JOIN chunks c ON c.id = v.chunk_id"
            ).fetchall()
            if rows:
                self.ids = np.array([r["cid"] for r in rows], dtype=np.int64)
                self.paper_of = np.array(
                    [r["pid"] for r in rows], dtype=np.int64
                )
                self.matrix = np.array(
                    [db.blob_to_vec(r["emb"]) for r in rows], dtype=np.float32
                )
            else:
                self.ids = np.array([], dtype=np.int64)
                self.paper_of = np.array([], dtype=np.int64)
                self.matrix = np.zeros((0, 1), dtype=np.float32)
            self.version = stamp
        return self.ids, self.matrix, self.paper_of


_INDEX = _VectorIndex()


def _paper_filter(
    conn: sqlite3.Connection,
    year_min: int | None,
    year_max: int | None,
    author: str | None,
    venue: str | None,
) -> set[int] | None:
    """Allowed paper ids for the given filters; None = no filtering."""
    conds: list[str] = []
    params: list[Any] = []
    if year_min is not None:
        conds.append("year >= ?")
        params.append(int(year_min))
    if year_max is not None:
        conds.append("year <= ?")
        params.append(int(year_max))
    if author:
        conds.append("authors LIKE ?")
        params.append(f"%{author}%")
    if venue:
        conds.append("venue LIKE ?")
        params.append(f"%{venue}%")
    if not conds:
        return None
    rows = conn.execute(
        f"SELECT id FROM papers WHERE {' AND '.join(conds)}", params
    )
    return {int(r["id"]) for r in rows}


def search(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = 5,
    *,
    per_paper: bool = True,
    embedder: EmbeddingClient | None = None,
    reranker: RerankerClient | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    author: str | None = None,
    venue: str | None = None,
) -> list[dict[str, Any]]:
    """Search the library. Never raises on embedding/rerank failure —
    silently degrades to FTS5 only."""
    allowed = _paper_filter(conn, year_min, year_max, author, venue)
    filtering = allowed is not None

    fts_pool = FILTER_OVERFETCH if filtering else 30
    fts_ids = db.search_fts(conn, query, k=fts_pool)
    if filtering and fts_ids:
        mapping = db.chunk_paper_map(conn, fts_ids)
        fts_ids = [i for i in fts_ids if mapping.get(i) in allowed]
    fts_ids = fts_ids[:30]

    vec_ids: list[int] = []
    if embedder is not None:
        try:
            qvec = embedder.embed([query])[0]
            ids, matrix, paper_of = _INDEX.get(conn)
            if len(ids):
                scores = _cosine(matrix, np.array(qvec, dtype=np.float32))
                depth = FILTER_OVERFETCH if filtering else 30
                order = np.argsort(-scores)[:depth]
                picked = ids[order].tolist()
                if filtering:
                    pos = {
                        int(cid): int(pid) for cid, pid in zip(ids, paper_of)
                    }
                    picked = [c for c in picked if pos.get(c) in allowed]
                vec_ids = picked[:30]
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
        if filtering and pid not in allowed:
            continue  # safety net; candidates were pre-filtered
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
