# -*- coding: utf-8 -*-
"""Concept graph (P2): LightRAG-style entity/relation extraction + dual-level search.

Build: chunks are sent to the LLM in small batches; each batch yields
entities (method/dataset/task/concept) and relations, deduplicated by
normalized name across batches. Every chunk is linked to the entities it
mentions (text containment; batches as fallback), so retrieval can map
entities back to evidence chunks.

Search (dual-level, LightRAG-inspired):
  1. seed entities   — name containment in the query + embedding match
  2. one-hop expand  — neighbors via kg_edges at decayed score
  3. evidence chunks — chunks linked to matched entities, aggregated per
     paper exactly like the two-stage pipeline
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

import numpy as np

from . import db
from .llm import chat, _parse_json_loose
from .util import log

BATCH_CHARS = 3
EXTRACT_MAX_CHARS = 1400
SEED_FETCH = 8
EXPAND_DECAY = 0.6
MIN_SIM = 0.42

EXTRACT_PROMPT = """你是学术知识图谱构建助手。从下面的论文片段中抽取实体与关系。
实体 type 只能是: method / dataset / task / concept。
只输出一个 JSON 对象，不要任何解释：
{"entities": [{"name": "英文术语", "type": "method", "desc": "一句话说明"}],
 "relations": [{"source": "实体1", "target": "实体2", "relation": "关系短语"}]}
要求：实体名保留论文原文的英文术语；source/target 必须来自 entities 列表；
每段最多 8 个实体、8 条关系。

论文片段：
"""


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _extract_batch(texts: list[str]) -> dict[str, Any]:
    prompt = EXTRACT_PROMPT + "\n\n".join(
        f"[片段{i + 1}] {t}" for i, t in enumerate(texts)
    )
    out = None
    for attempt in (1, 2):  # 中转网关偶发超时，重试一次
        try:
            out = chat(prompt, max_tokens=900, timeout=240)
            break
        except Exception as exc:
            log(f"  [KG 抽取重试 {attempt}] {type(exc).__name__}: {str(exc)[:100]}")
    if out is None:
        return {}
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end <= start:
        return {}
    data = _parse_json_loose(out[start : end + 1])
    return data if isinstance(data, dict) else {}


class _NodeVecIndex:
    """Entity name+desc embeddings, cached by a version stamp."""

    def __init__(self) -> None:
        self.version: tuple[int, int] | None = None
        self.ids = np.array([], dtype=np.int64)
        self.matrix = np.zeros((0, 1), dtype=np.float32)

    def get(self, conn: sqlite3.Connection, embedder) -> tuple[np.ndarray, np.ndarray]:
        row = conn.execute(
            "SELECT COUNT(*) c, COALESCE(MAX(id), 0) m FROM kg_nodes"
        ).fetchone()
        stamp = (int(row["c"]), int(row["m"]))
        if stamp != self.version and len(stamp) and stamp[0]:
            try:
                rows = conn.execute(
                    "SELECT id, display, desc FROM kg_nodes"
                ).fetchall()
                texts = [f"{r['display']} — {r['desc']}"[:512] for r in rows]
                vecs = embedder.embed(texts)
                self.ids = np.array([r["id"] for r in rows], dtype=np.int64)
                self.matrix = np.array(vecs, dtype=np.float32)
                self.version = stamp
            except Exception as exc:
                log(f"[KG 向量跳过] {type(exc).__name__}: {str(exc)[:120]}")
        return self.ids, self.matrix


_NODE_INDEX = _NodeVecIndex()


def build_paper_kg(conn: sqlite3.Connection, paper_id: int) -> dict[str, Any]:
    """Extract entities/relations for one paper from all of its chunks."""
    row = db.get_paper(conn, paper_id)
    if not row:
        raise ValueError(f"paper_id={paper_id} 不存在")
    chunks = conn.execute(
        "SELECT id, text FROM chunks WHERE paper_id = ? ORDER BY ord",
        (paper_id,),
    ).fetchall()
    if not chunks:
        return {"paper_id": paper_id, "title": row["title"], "status": "empty"}

    n_entities = n_relations = n_batches = 0
    pending_links: dict[int, set[int]] = {}
    for i in range(0, len(chunks), BATCH_CHARS):
        batch = chunks[i : i + BATCH_CHARS]
        texts = [c["text"][:EXTRACT_MAX_CHARS] for c in batch]
        try:
            data = _extract_batch(texts)
        except Exception as exc:
            log(f"  [KG 批次跳过] {i}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        if not data:
            continue
        n_batches += 1
        chunk_ids = [c["id"] for c in batch]
        name_to_id: dict[str, int] = {}
        for ent in data.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            name = str(ent.get("name") or "").strip()
            if len(_norm(name)) < 2:
                continue
            nid = db.get_or_create_kg_node(
                conn, name, name,
                str(ent.get("type") or "concept"),
                str(ent.get("desc") or ""),
            )
            name_to_id[_norm(name)] = nid
            # 精确归因：实体名出现在哪个片段就链哪个；都找不到则链整批
            linked = [
                cid for cid, t in zip(chunk_ids, texts)
                if _norm(name) in _norm(t)
            ] or chunk_ids
            for cid in linked:
                pending_links.setdefault(cid, set()).add(nid)
            n_entities += 1
        for rel in data.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            s_name, t_name = _norm(str(rel.get("source") or "")), _norm(str(rel.get("target") or ""))
            if len(s_name) < 2 or len(t_name) < 2:
                continue
            # 关系端点允许引用之前批次已抽取的实体
            s = name_to_id.get(s_name) or db.get_or_create_kg_node(conn, str(rel.get("source")))
            t = name_to_id.get(t_name) or db.get_or_create_kg_node(conn, str(rel.get("target")))
            if s and t and s != t:
                db.upsert_kg_edge(
                    conn, s, t, str(rel.get("relation") or "相关")[:60],
                    paper_id, batch[0]["id"],
                )
                n_relations += 1

    for cid, ids in pending_links.items():
        db.set_chunk_links(conn, cid, sorted(ids))
    db.set_kg_built(conn, paper_id)
    conn.commit()
    return {
        "paper_id": paper_id,
        "title": row["title"],
        "status": "ok",
        "batches": n_batches,
        "entities": n_entities,
        "relations": n_relations,
        "chunks_linked": len(pending_links),
    }


def search_graph(
    conn: sqlite3.Connection, query: str, top_k: int = 5,
    embedder=None,
) -> dict[str, Any]:
    """Dual-level concept search: entity seeds → 1-hop expansion →
    evidence chunks aggregated per paper."""
    nodes = conn.execute(
        "SELECT id, name, display, type, desc FROM kg_nodes"
    ).fetchall()
    if not nodes:
        return {"entities": [], "edges": [], "papers": []}

    qn = _norm(query)
    scored: dict[int, float] = {}
    for r in nodes:
        if len(r["name"]) >= 3 and r["name"] in qn:
            scored[r["id"]] = 1.0
    if embedder is not None and len(scored) < SEED_FETCH:
        try:
            ids, matrix = _NODE_INDEX.get(conn, embedder)
            if len(ids):
                qv = np.array(embedder.embed([query])[0], dtype=np.float32)
                qnorm = np.linalg.norm(qv) or 1.0
                mnorms = np.linalg.norm(matrix, axis=1)
                mnorms[mnorms == 0] = 1.0
                sims = (matrix @ qv) / (mnorms * qnorm)
                order = np.argsort(-sims)[:SEED_FETCH]
                for i in order:
                    if sims[i] >= MIN_SIM:
                        scored[int(ids[i])] = max(
                            scored.get(int(ids[i]), 0.0), float(sims[i])
                        )
        except Exception:
            pass
    if not scored:
        return {"entities": [], "edges": [], "papers": []}

    # one-hop expansion with decayed score
    node_rows = {r["id"]: r for r in nodes}
    all_edges = conn.execute(
        "SELECT src, dst, relation FROM kg_edges"
    ).fetchall()
    for e in all_edges:
        for a, b in ((e["src"], e["dst"]), (e["dst"], e["src"])):
            if a in scored and b not in scored:
                scored[b] = scored[a] * EXPAND_DECAY

    top_entities = sorted(scored.items(), key=lambda kv: -kv[1])[:12]
    entity_ids = {eid for eid, _ in top_entities}

    # evidence chunks → papers
    chunk_score: dict[int, float] = {}
    for r in conn.execute("SELECT chunk_id, node_ids FROM kg_chunk_links"):
        try:
            ids = json.loads(r["node_ids"])
        except Exception:
            continue
        s = sum(scored.get(i, 0.0) for i in ids)
        if s > 0:
            chunk_score[r["chunk_id"]] = max(chunk_score.get(r["chunk_id"], 0.0), s)

    papers: list[dict[str, Any]] = []
    if chunk_score:
        per_paper: dict[int, tuple[int, float]] = {}
        for cid, s in chunk_score.items():
            pid = conn.execute(
                "SELECT paper_id FROM chunks WHERE id = ?", (cid,)
            ).fetchone()
            if not pid:
                continue
            pid = pid["paper_id"]
            if pid not in per_paper or s > per_paper[pid][1]:
                per_paper[pid] = (cid, s)
        best_ids = [cid for cid, _ in per_paper.values()]
        rows = db.chunks_by_ids(conn, best_ids)
        by_id = {r["id"]: r for r in rows}
        ranked = sorted(per_paper.items(), key=lambda kv: -kv[1][1])[:top_k]
        for pid, (cid, s) in ranked:
            r = by_id.get(cid)
            if r:
                papers.append({
                    "paper_id": pid,
                    "title": r["title"],
                    "year": r["year"],
                    "summary": (r["paper_summary"] or "")[:400],
                    "score": round(s, 3),
                    "section": r["section"],
                    "snippet": r["text"][:300],
                })

    entities = [
        {
            "id": eid,
            "name": node_rows[eid]["display"],
            "type": node_rows[eid]["type"],
            "desc": node_rows[eid]["desc"],
            "score": round(s, 3),
        }
        for eid, s in top_entities
    ]
    eid_set = set(entity_ids)
    edges = [
        {"src": e["src"], "dst": e["dst"], "relation": e["relation"]}
        for e in all_edges
        if e["src"] in eid_set and e["dst"] in eid_set
    ]
    return {"entities": entities, "edges": edges, "papers": papers}


def format_graph_result(res: dict[str, Any], query: str) -> str:
    if not res["entities"] and not res["papers"]:
        return f"概念图中没有与 “{query}” 相关的实体。先运行 build-kg 构建概念图。"
    lines = [f"概念检索 “{query}”：", ""]
    if res["entities"]:
        lines.append("## 相关概念")
        lines += [
            f"- {e['name']}（{e['type']}，相关度 {e['score']}）"
            + (f"：{e['desc']}" if e["desc"] else "")
            for e in res["entities"][:8]
        ]
    if res["papers"]:
        lines.append("")
        lines.append("## 命中论文（按概念相关度）")
        lines += [
            f"- [{p['paper_id']}] {p['title']}"
            + (f" ({p['year']})" if p["year"] else "")
            + f" — {p['section']}"
            for p in res["papers"]
        ]
    lines.append("")
    lines.append("用 read_paper_section(paper_id, section) 深入阅读。")
    return "\n".join(lines)
