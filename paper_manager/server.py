# -*- coding: utf-8 -*-
"""Paper manager web UI: timeline graph + search + import.

    python -m paper_manager.server                 # 127.0.0.1:8830
    python -m paper_manager.server --host 0.0.0.0  # reachable from Tailscale

The graph merges two relation kinds:
  citation edges   — from the citations table (fetch via CLI
                     fetch-citations or the panel button)
  similarity edges — top cosine neighbors among paper vectors (dashed)
Layout is a timeline: x = publication year, lanes = citation clusters,
so the field's development reads left to right.
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from . import db, retriever, scholar
from .config import ROOT
from .embedder import EmbeddingClient, RerankerClient

app = FastAPI(title="Paper Manager")

_SIM_THRESHOLD = 0.55
_SIM_TOP = 2


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(ROOT) / "static" / "index.html")


@app.get("/api/status")
def status() -> dict:
    conn = db.connect()
    try:
        s = db.stats(conn)
        s["db_path"] = str(db.DB_PATH)
        return s
    finally:
        conn.close()


@app.get("/api/papers")
def papers() -> list[dict]:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, authors, year, venue, doi, abstract, summary,"
            " engine, added_at, citations_fetched_at FROM papers ORDER BY year, id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _similarity_edges(conn, sim_threshold: float) -> list[dict]:
    from .retriever import _PAPER_INDEX, _cosine

    ids, matrix = _PAPER_INDEX.get(conn)
    edges: dict[tuple[int, int], float] = {}
    if len(ids) > 1:
        sims = np.array(
            [_cosine(matrix, matrix[i]) for i in range(len(ids))],
            dtype=np.float32,
        )
        for i in range(len(ids)):
            order = [
                j for j in (-sims[i]).argsort()[: _SIM_TOP + 1]
                if j != i and sims[i][j] >= sim_threshold
            ]
            for j in order:
                a, b = int(ids[i]), int(ids[j])
                key = (min(a, b), max(a, b))
                edges[key] = max(edges.get(key, 0.0), float(sims[i][j]))
    return [
        {"src": s, "dst": d, "kind": "similar", "weight": round(w, 3)}
        for (s, d), w in sorted(edges.items())
    ]


@app.get("/api/graph")
def graph(sim: float = _SIM_THRESHOLD) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, authors, year, summary, citations_fetched_at "
            "FROM papers ORDER BY year, id"
        ).fetchall()
        nodes = [
            {
                "id": r["id"],
                "title": r["title"],
                "authors": r["authors"],
                "year": r["year"],
                "summary": (r["summary"] or "")[:220],
                "has_citations": r["citations_fetched_at"] is not None,
            }
            for r in rows
        ]
        edges = [
            {"src": e["src"], "dst": e["dst"], "kind": "citation"}
            for e in db.internal_citation_edges(conn)
        ]
        edges += _similarity_edges(conn, float(sim))
        for n in nodes:
            n["cluster"] = 0
        _assign_clusters(nodes, edges)
        return {"nodes": nodes, "edges": edges}
    finally:
        conn.close()


def _assign_clusters(nodes: list[dict], edges: list[dict]) -> None:
    """Connected components over citation edges become lane/color groups."""
    parent = {n["id"]: n["id"] for n in nodes}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in edges:
        if e["kind"] != "citation":
            continue
        a, b = find(e["src"]), find(e["dst"])
        if a != b:
            parent[a] = b

    roots = {}
    for n in nodes:
        root = find(n["id"])
        n["cluster"] = roots.setdefault(root, len(roots))


@app.get("/api/paper/{paper_id}")
def paper_detail(paper_id: int) -> dict:
    conn = db.connect()
    try:
        row = db.get_paper(conn, paper_id)
        if not row:
            raise HTTPException(404, "paper not found")
        rel = retriever.related_papers(
            conn, paper_id, top_k=6, embedder=EmbeddingClient.from_env()
        )
        cite_counts = conn.execute(
            "SELECT direction, COUNT(*) c FROM citations WHERE paper_id = ? "
            "GROUP BY direction",
            (paper_id,),
        ).fetchall()
        return {
            **dict(row),
            "cites_in_lib": len(rel["cites"]),
            "cited_by_in_lib": len(rel["cited_by"]),
            "related": rel,
            "ext_counts": {r["direction"]: r["c"] for r in cite_counts},
        }
    finally:
        conn.close()


@app.get("/api/search")
def search(q: str, top_k: int = 8) -> dict:
    if not q.strip():
        return {"hits": []}
    conn = db.connect()
    try:
        hits = retriever.search(
            conn,
            q.strip(),
            top_k=max(1, min(top_k, 20)),
            embedder=EmbeddingClient.from_env(),
            reranker=RerankerClient.from_env(),
        )
        return {"hits": hits}
    finally:
        conn.close()


@app.post("/api/ingest")
async def ingest(
    file: UploadFile = File(...), engine: str = Form("datalab")
) -> JSONResponse:
    if engine not in ("datalab", "local"):
        raise HTTPException(400, "engine must be datalab|local")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    suffix = Path(file.filename or "upload.pdf").suffix or ".pdf"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        from .ingest import ingest_pdf

        # 分钟级的转换/轮询必须丢进线程池，否则整个事件循环被卡死
        report = await run_in_threadpool(
            ingest_pdf, tmp_path,
            engine=engine, embedder=EmbeddingClient.from_env(),
        )
        return JSONResponse(report)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/fetch-citations/{paper_id}")
def fetch_citations(paper_id: int) -> dict:
    conn = db.connect()
    try:
        return scholar.fetch_citations(conn, paper_id)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper manager web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8830)
    args = ap.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
