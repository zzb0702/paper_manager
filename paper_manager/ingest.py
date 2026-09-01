# -*- coding: utf-8 -*-
"""Ingest pipeline: PDF -> Markdown -> metadata -> chunks -> vectors -> DB."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import db
from .chunker import chunk_markdown
from .config import MD_DIR, ensure_dirs
from .convert import convert_datalab, convert_local, guess_title
from .embedder import EmbeddingClient
from .llm import summarize_paper
from .util import log

EMBED_BATCH = 16

_ABSTRACT_RE = re.compile(
    r"(?is)abstract[:\.\s—-]*(.{80,2500}?)"
    r"(?:\n#|\n\s*\n\s*(?:1|introduction|keywords|index terms)\b)"
)


def _extract_abstract(front_text: str) -> str:
    m = _ABSTRACT_RE.search(front_text)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:1200] if m else ""


def _embed_all(
    texts: list[str], embedder: EmbeddingClient | None
) -> list[list[float] | None]:
    if embedder is None:
        return [None] * len(texts)
    vectors: list[list[float] | None] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [t[:4000] or " " for t in texts[i : i + EMBED_BATCH]]
        try:
            vectors.extend(embedder.embed(batch))
        except Exception as exc:
            log(f"  [向量跳过] 批次 {i}: {type(exc).__name__}: {exc}")
            vectors.extend([None] * len(batch))
    return vectors


def ingest_pdf(
    pdf_path: str | Path,
    *,
    engine: str = "datalab",
    force: bool = False,
    embedder: EmbeddingClient | None = None,
    make_summary: bool = True,
) -> dict[str, Any]:
    """Ingest one PDF. Returns a report dict; raises only on fatal errors."""
    pdf_path = Path(pdf_path).resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")
    ensure_dirs()

    import sqlite3

    conn = db.connect()
    sha = db.sha256_of(pdf_path)
    existing = db.find_by_sha(conn, sha)
    if existing and not force:
        return {
            "status": "duplicate",
            "paper_id": existing["id"],
            "title": existing["title"],
        }

    log(f"[1/5] 转换 PDF（引擎: {engine}）: {pdf_path.name}")
    if engine == "local":
        conv = convert_local(pdf_path)
    else:
        conv = convert_datalab(pdf_path)
        if conv.get("cost_usd") is not None:
            log(
                f"  [计费] {conv.get('page_count')} 页，"
                f"${conv['cost_usd']:.4f}（key#{conv.get('key_index')}，{conv.get('keys_available')}）"
            )
    markdown = conv["markdown"]
    meta = conv["meta"] or {}
    front = conv["front_text"]

    log(f"[2/5] 提取元数据（{conv['page_count']} 页）")
    title = guess_title(markdown, meta)
    authors = (meta.get("author") or "")[:300]
    year = db.extract_year(front, meta)
    doi = db.extract_doi(front)
    abstract = _extract_abstract(front)

    md_file = MD_DIR / f"{sha[:16]}.md"
    md_file.write_text(markdown, encoding="utf-8")

    log(f"[3/5] 生成结构化摘要: {title[:60]}")
    summary = ""
    if make_summary:
        summary = summarize_paper(abstract and (title + "\n" + abstract) or front) or ""

    chunks = chunk_markdown(markdown)
    log(f"[4/5] 切块完成: {len(chunks)} 块，开始嵌入")
    vectors = _embed_all([c["text"] for c in chunks], embedder)

    if existing and force:
        # fts tables have no FK cascade; clean them explicitly
        db.delete_paper_fts(conn, existing["id"])
        db.delete_paper_index(conn, existing["id"])
        conn.execute("DELETE FROM papers WHERE id = ?", (existing["id"],))
        conn.commit()

    paper_id = db.insert_paper(
        conn,
        sha256=sha,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        abstract=abstract,
        summary=summary,
        pdf_path=str(pdf_path),
        md_path=str(md_file),
        engine=engine,
    )
    n = db.replace_chunks(conn, paper_id, chunks, vectors)

    # stage-1 index: paper FTS row + one paper-level vector
    paper_vector = None
    if embedder is not None:
        try:
            paper_text = (
                f"{title}\n{abstract}\n{summary}".strip() or markdown[:1500]
            )
            paper_vector = embedder.embed([paper_text[:2000]])[0]
        except Exception as exc:
            log(f"  [论文向量跳过] {type(exc).__name__}: {str(exc)[:120]}")
    index_text = " / ".join(
        p for p in (title, authors, abstract, summary) if p
    ) or markdown[:1500]
    db.upsert_paper_index(conn, paper_id, index_text, paper_vector)

    log(f"[5/5] 入库完成: paper_id={paper_id}, {n} chunks")
    conn.close()
    return {
        "status": "ok",
        "paper_id": paper_id,
        "title": title,
        "year": year,
        "doi": doi,
        "chunks": n,
        "embedded": sum(1 for v in vectors if v),
        "summary_chars": len(summary),
        "engine": engine,
        "cost_usd": conv.get("cost_usd"),
        "paper_vector": paper_vector is not None,
    }


def ingest_dir(
    directory: str | Path, *, engine: str = "local", **kw: Any
) -> list[dict[str, Any]]:
    directory = Path(directory)
    pdfs = sorted(directory.rglob("*.pdf"))
    reports = []
    for p in pdfs:
        try:
            r = ingest_pdf(p, engine=engine, **kw)
        except Exception as exc:
            r = {"status": "error", "path": str(p), "error": f"{type(exc).__name__}: {exc}"}
        reports.append(r)
        log(f"  -> {r.get('status')}: {p.name}")
    return reports
