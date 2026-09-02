# -*- coding: utf-8 -*-
"""Two-stage hybrid retrieval.

Stage 1  paper-level recall: FTS over papers_fts + one vector per paper
         (title/abstract/summary), fused by RRF -> candidate papers.
Stage 2  chunk-level search restricted to candidate papers, again
         FTS + vector + RRF, with optional multi-query expansion
         (LLM query rewriting) merged into the same fusion.
Ordering paper score = best chunk fused score, boosted ~10% per extra
matched chunk (cap 5) — a paper relevant in many places outranks one
with a single lucky hit. Reranker scores the best chunk of each paper
and overrides the ordering when available.

Both stages degrade gracefully: no paper vectors -> FTS-only stage 1;
empty stage 1 -> legacy global chunk search; no embedder -> FTS only.

All vectors live in in-process numpy matrices keyed by cheap
(COUNT, MAX(rowid)) version stamps, so a long-lived MCP server reloads
only when a table actually changes (any process, e.g. the CLI ingesting).
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

import numpy as np

from . import db
from .embedder import EmbeddingClient, RerankerClient
from .util import log

RRF_K = 60
PAPER_POOL = 20          # stage-1 candidate papers
CAND_POOL = 20           # stage-2 candidate chunks before aggregation
FETCH = 30               # per-query per-channel fetch depth
FILTER_OVERFETCH = 200   # deeper pools when metadata filtering is active
SNIPPET_CHARS = 400
AGG_BOOST = 0.10         # per extra matched chunk, capped at 5 extras
MAX_QUERIES = 4          # original + up to 3 rewritten variants

QueryRewriter = Callable[[str], "list[str] | None"]


def _cosine(matrix: np.ndarray, query: np.ndarray) -> np.ndarray:
    qnorm = np.linalg.norm(query) or 1.0
    mnorms = np.linalg.norm(matrix, axis=1)
    mnorms[mnorms == 0] = 1.0
    return (matrix @ query) / (mnorms * qnorm)


def _rrf_add(fused: dict[int, float], ids: list[Any]) -> None:
    for rank, cid in enumerate(ids):
        fused[int(cid)] = fused.get(int(cid), 0.0) + 1.0 / (RRF_K + rank + 1)


class _VectorIndex:
    """Process-wide cache of chunk vectors, aligned with chunk ids and
    their owning paper ids."""

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


class _PaperVecIndex:
    """Process-wide cache of paper-level vectors (same stamp pattern)."""

    def __init__(self) -> None:
        self.version: tuple[int, int] | None = None
        self.ids = np.array([], dtype=np.int64)
        self.matrix = np.zeros((0, 1), dtype=np.float32)

    def get(self, conn: sqlite3.Connection) -> tuple[np.ndarray, np.ndarray]:
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(MAX(rowid), 0) m FROM paper_vectors"
        ).fetchone()
        stamp = (int(row["c"]), int(row["m"]))
        if stamp != self.version:
            rows = conn.execute(
                "SELECT paper_id, embedding FROM paper_vectors"
            ).fetchall()
            if rows:
                self.ids = np.array(
                    [r["paper_id"] for r in rows], dtype=np.int64
                )
                self.matrix = np.array(
                    [db.blob_to_vec(r["embedding"]) for r in rows],
                    dtype=np.float32,
                )
            else:
                self.ids = np.array([], dtype=np.int64)
                self.matrix = np.zeros((0, 1), dtype=np.float32)
            self.version = stamp
        return self.ids, self.matrix


_INDEX = _VectorIndex()
_PAPER_INDEX = _PaperVecIndex()


def _backfill_paper_index(
    conn: sqlite3.Connection, embedder: EmbeddingClient | None
) -> None:
    """Legacy rows get a papers_fts row on first search; vectors too when
    an embedding client is available (same pattern as the memory system)."""
    missing_fts = db.papers_missing_fts(conn)
    if missing_fts:
        for pid in missing_fts:
            row = db.get_paper(conn, pid)
            if row:
                db.upsert_paper_index(
                    conn, pid, db.paper_index_text(row), None
                )
        log(f"[论文索引] 补齐 {len(missing_fts)} 篇的 FTS 行")
    if embedder is not None:
        missing_vecs = db.papers_missing_vectors(conn)
        if missing_vecs:
            texts = [db.paper_index_text(r)[:2000] or " " for r in missing_vecs]
            try:
                vectors = embedder.embed(texts)
                for row, vec in zip(missing_vecs, vectors):
                    db.upsert_paper_index(
                        conn, row["id"], db.paper_index_text(row), vec
                    )
                log(f"[论文索引] 补齐 {len(missing_vecs)} 篇的论文向量")
            except Exception as exc:
                log(f"[论文向量跳过] {type(exc).__name__}: {str(exc)[:150]}")


def _paper_filter(
    conn: sqlite3.Connection,
    year_min: int | None,
    year_max: int | None,
    author: str | None,
    venue: str | None,
) -> set[int] | None:
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


def _expand_queries(
    query: str, query_rewriter: QueryRewriter | None
) -> list[str]:
    queries = [query]
    if query_rewriter is not None:
        try:
            variants = query_rewriter(query) or []
        except Exception:
            variants = []
        for v in variants:
            v = str(v).strip()
            if v and v != query and v not in queries:
                queries.append(v)
    return queries[:MAX_QUERIES]


def _stage1_candidates(
    conn: sqlite3.Connection,
    queries: list[str],
    allowed: set[int] | None,
    embedder: EmbeddingClient | None,
) -> list[int]:
    fused: dict[int, float] = {}
    for q in queries:
        _rrf_add(fused, db.search_papers_fts(conn, q, k=FETCH))
        if embedder is not None:
            try:
                qvec = np.array(embedder.embed([q])[0], dtype=np.float32)
                ids, matrix = _PAPER_INDEX.get(conn)
                if len(ids):
                    scores = _cosine(matrix, qvec)
                    order = np.argsort(-scores)[:FETCH]
                    _rrf_add(fused, ids[order].tolist())
            except Exception:
                pass
    if allowed is not None:
        fused = {p: s for p, s in fused.items() if p in allowed}
    return sorted(fused, key=lambda p: -fused[p])[:PAPER_POOL]


def _hit_card(row: sqlite3.Row, score: float, matched: int) -> dict[str, Any]:
    pages = ""
    if row["page_start"] is not None:
        pages = (
            str(row["page_start"])
            if row["page_start"] == row["page_end"]
            else f"{row['page_start']}-{row['page_end']}"
        )
    return {
        "paper_id": row["paper_id"],
        "title": row["title"],
        "year": row["year"],
        "summary": (row["paper_summary"] or "")[:500],
        "chunk_id": row["id"],
        "section": row["section"],
        "pages": pages,
        "score": round(float(score), 5),
        "matched_chunks": matched,
        "snippet": row["text"][:SNIPPET_CHARS],
    }


def _cards_from_rows(
    rows: list[sqlite3.Row], scores: dict[int, float], matched: dict[int, int]
) -> list[dict[str, Any]]:
    return [
        _hit_card(r, scores.get(r["id"], 0.0), matched.get(r["id"], 1))
        for r in rows
    ]


def _global_chunk_search(
    conn: sqlite3.Connection,
    queries: list[str],
    allowed: set[int] | None,
    top_k: int,
    per_paper: bool,
    embedder: EmbeddingClient | None,
    reranker: RerankerClient | None,
) -> list[dict[str, Any]]:
    """Legacy path: chunk-level search over the whole library."""
    fused: dict[int, float] = {}
    for q in queries:
        depth = FILTER_OVERFETCH if allowed is not None else FETCH
        fts_ids = db.search_fts(conn, q, k=depth)
        if allowed is not None and fts_ids:
            mapping = db.chunk_paper_map(conn, fts_ids)
            fts_ids = [i for i in fts_ids if mapping.get(i) in allowed]
        _rrf_add(fused, fts_ids[:FETCH])

        if embedder is not None:
            try:
                qvec = np.array(embedder.embed([q])[0], dtype=np.float32)
                ids, matrix, paper_of = _INDEX.get(conn)
                if len(ids):
                    scores = _cosine(matrix, qvec)
                    d = FILTER_OVERFETCH if allowed is not None else FETCH
                    order = np.argsort(-scores)[:d]
                    picked = ids[order].tolist()
                    if allowed is not None:
                        pos = {
                            int(c): int(p) for c, p in zip(ids, paper_of)
                        }
                        picked = [c for c in picked if pos.get(c) in allowed]
                    _rrf_add(fused, picked[:FETCH])
            except Exception:
                pass

    if not fused:
        return []
    pool = sorted(fused.items(), key=lambda kv: -kv[1])[:CAND_POOL]
    pool_ids = [cid for cid, _ in pool]
    rows = db.chunks_by_ids(conn, pool_ids)
    if allowed is not None:
        rows = [r for r in rows if r["paper_id"] in allowed]

    if reranker is not None and len(rows) > 1:
        try:
            ranked = reranker.rerank(
                query=queries[0],
                documents=[r["text"][:1500] for r in rows],
                top_n=min(len(rows), max(top_k * 2, 8)),
            )
            if ranked:
                order = {i["index"]: i["score"] for i in ranked}
                rows = [
                    r
                    for _, r in sorted(
                        enumerate(rows), key=lambda kv: -order.get(kv[0], 0.0)
                    )
                ]
        except Exception:
            pass

    seen: set[int] = set()
    results: list[dict[str, Any]] = []
    for r in rows:
        if per_paper:
            if r["paper_id"] in seen:
                continue
            seen.add(r["paper_id"])
        results.append(_hit_card(r, fused.get(r["id"], 0.0), 1))
        if len(results) >= top_k:
            break
    return results


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
    query_rewriter: QueryRewriter | None = None,
) -> list[dict[str, Any]]:
    """Two-stage search. Never raises on embedding/rerank/rewrite failure —
    silently degrades to fewer signals, down to plain FTS5."""
    queries = _expand_queries(query, query_rewriter)
    allowed = _paper_filter(conn, year_min, year_max, author, venue)
    if len(queries) > 1:
        log(f"[查询改写] {queries}")

    if per_paper:
        _backfill_paper_index(conn, embedder)
        candidates = _stage1_candidates(conn, queries, allowed, embedder)
        if candidates:
            hits = _stage2(conn, queries, candidates, top_k, embedder, reranker)
            if hits:
                return hits[:top_k]
        # 阶段二为空（论文级命中但章节级全不匹配，如 chunk 无向量且
        # 查询词只在摘要里）→ 回退全局章节检索

    return _global_chunk_search(
        conn, queries, allowed, top_k, per_paper, embedder, reranker
    )


def _stage2(
    conn: sqlite3.Connection,
    queries: list[str],
    candidates: list[int],
    top_k: int,
    embedder: EmbeddingClient | None,
    reranker: RerankerClient | None,
) -> list[dict[str, Any]]:
    fused: dict[int, float] = {}
    for q in queries:
        _rrf_add(
            fused, db.search_fts(conn, q, k=FETCH, paper_ids=candidates)
        )
        if embedder is not None:
            try:
                ids, matrix, paper_of = _INDEX.get(conn)
                mask = np.isin(paper_of, np.array(candidates, dtype=np.int64))
                if mask.any():
                    sub_ids, sub_matrix = ids[mask], matrix[mask]
                    qvec = np.array(embedder.embed([q])[0], dtype=np.float32)
                    scores = _cosine(sub_matrix, qvec)
                    order = np.argsort(-scores)[:FETCH]
                    _rrf_add(fused, sub_ids[order].tolist())
            except Exception:
                pass

    if not fused:
        return []

    # aggregate: paper score = best chunk + boost per extra matched chunk
    mapping = db.chunk_paper_map(conn, list(fused.keys()))
    by_paper: dict[int, list[int]] = {}
    for cid, score in fused.items():
        pid = mapping.get(cid)
        if pid is not None:
            by_paper.setdefault(pid, []).append(cid)

    scored: list[tuple[int, int, float]] = []  # (paper_id, best_chunk, score)
    for pid, cids in by_paper.items():
        best_cid = max(cids, key=lambda c: fused[c])
        extra = len(cids) - 1
        score = fused[best_cid] * (1 + AGG_BOOST * min(extra, 5))
        scored.append((pid, best_cid, score))
    scored.sort(key=lambda t: -t[2])
    scored = scored[: max(top_k * 2, 8)]

    best_ids = [cid for _, cid, _ in scored]
    rows = db.chunks_by_ids(conn, best_ids)
    by_chunk = {r["id"]: r for r in rows}
    ordered_rows = [by_chunk[cid] for cid in best_ids if cid in by_chunk]
    ordered = list(zip(scored, ordered_rows))

    if reranker is not None and len(ordered) > 1:
        try:
            ranked = reranker.rerank(
                query=queries[0],
                documents=[r["text"][:1500] for _, r in ordered],
                top_n=len(ordered),
            )
            if ranked:
                scores = {i["index"]: i["score"] for i in ranked}
                ordered = sorted(
                    enumerate(ordered),
                    key=lambda kv: -scores.get(kv[0], 0.0),
                )
                ordered = [item for _, item in ordered]
        except Exception:
            pass  # keep aggregated order

    matched = {
        pid: len(cids) for pid, cids in by_paper.items()
    }
    results = []
    for (pid, _best, pscore), row in ordered:
        card = _hit_card(row, pscore, matched.get(pid, 1))
        results.append(card)
    return results


def related_papers(
    conn: sqlite3.Connection,
    paper_id: int,
    top_k: int = 5,
    embedder: EmbeddingClient | None = None,
) -> dict[str, Any]:
    """Neighbors of a paper: citation graph (library-internal) + semantic
    neighbors via paper vectors. Powers the MCP related_papers tool and
    the UI detail panel."""
    neighbors = db.library_neighbors(conn, paper_id)
    row = db.get_paper(conn, paper_id)

    def _cards(pids: list[int]) -> list[dict[str, Any]]:
        cards = []
        for pid in pids:
            p = db.get_paper(conn, pid)
            if p:
                cards.append(
                    {"paper_id": pid, "title": p["title"], "year": p["year"]}
                )
        return cards

    semantic: list[dict[str, Any]] = []
    if embedder is not None:
        try:
            _backfill_paper_index(conn, embedder)
            ids, matrix = _PAPER_INDEX.get(conn)
            if len(ids):
                idx = np.where(ids == paper_id)[0]
                if len(idx):
                    qvec = matrix[idx[0]]
                else:
                    qvec = np.array(
                        embedder.embed([db.paper_index_text(row)[:2000] or " "])[0],
                        dtype=np.float32,
                    )
                scores = _cosine(matrix, qvec)
                order = [
                    i for i in np.argsort(-scores)
                    if int(ids[i]) != paper_id
                ][:top_k]
                semantic = [
                    {
                        "paper_id": int(ids[i]),
                        "title": db.get_paper(conn, int(ids[i]))["title"],
                        "year": db.get_paper(conn, int(ids[i]))["year"],
                        "similarity": round(float(scores[i]), 3),
                    }
                    for i in order
                ]
        except Exception:
            pass

    return {
        "paper_id": paper_id,
        "title": row["title"],
        "cites": _cards([n["paper_id"] for n in neighbors["cites"]]),
        "cited_by": _cards([n["paper_id"] for n in neighbors["cited_by"]]),
        "semantic": semantic,
    }


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
        lines.append(f"匹配位置：{cite}（{h['matched_chunks']} 处相关段落）")
        lines.append(f"片段：{h['snippet']}…")
        lines.append("")
    lines.append("用 read_paper_section(paper_id, section) 深入阅读某个章节。")
    return "\n".join(lines)
