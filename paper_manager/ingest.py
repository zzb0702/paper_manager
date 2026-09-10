# -*- coding: utf-8 -*-
"""Ingest pipeline: PDF -> Markdown -> metadata -> chunks -> vectors -> DB."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import db
from .chunker import chunk_markdown
from .config import MD_DIR, ensure_dirs
from .convert import (
    convert_datalab,
    convert_local,
    extract_authors,
    extract_title,
    guess_title,
)
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
    title = extract_title(front, meta) or guess_title(markdown, meta)
    authors = extract_authors(front, meta)
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

    # Best-effort post-ingest fixes: missing authors via OpenAlex/S2,
    # missing summary via one more LLM call. Never fails the ingest.
    authors, year, summary, index_dirty = enrich_after_ingest(
        conn,
        paper_id=paper_id,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        abstract=abstract,
        summary=summary,
        front=front,
        make_summary=make_summary,
    )
    if index_dirty:
        paper = db.get_paper(conn, paper_id)
        if paper:
            db.upsert_paper_fts(conn, paper_id, db.paper_index_text(paper))

    log(f"[5/5] 入库完成: paper_id={paper_id}, {n} chunks")
    conn.close()
    return {
        "status": "ok",
        "paper_id": paper_id,
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "chunks": n,
        "embedded": sum(1 for v in vectors if v),
        "summary_chars": len(summary),
        "engine": engine,
        "cost_usd": conv.get("cost_usd"),
        "paper_vector": paper_vector is not None,
    }


def enrich_after_ingest(
    conn: Any,
    *,
    paper_id: int,
    title: str,
    authors: str,
    year: int | None,
    abstract: str,
    summary: str,
    front: str,
    doi: str = "",
    make_summary: bool = True,
    force_summary: bool = False,
) -> tuple[str, int | None, str, bool]:
    """Fill empty authors / summary after insert. Returns updated fields + dirty flag."""
    dirty = False
    authors = (authors or "").strip()
    summary = (summary or "").strip()

    if not authors:
        try:
            from . import scholar

            meta = scholar.lookup_metadata(title, doi=doi or "")
            if meta and meta.get("authors"):
                authors = str(meta["authors"]).strip()[:300]
                db.set_authors(conn, paper_id, authors)
                if meta.get("year") and not year:
                    year = int(meta["year"])
                    conn.execute(
                        "UPDATE papers SET year = ? WHERE id = ?", (year, paper_id)
                    )
                    conn.commit()
                dirty = True
                log(f"  [元数据补齐] 作者来自 {meta.get('source')}: {authors[:60]}")
        except Exception as exc:
            log(f"  [元数据补齐跳过] {type(exc).__name__}: {str(exc)[:120]}")

    if make_summary and (force_summary or not summary):
        summary = summarize_paper(abstract and (title + "\n" + abstract) or front) or ""
        if summary:
            conn.execute(
                "UPDATE papers SET summary = ? WHERE id = ?", (summary, paper_id)
            )
            conn.commit()
            dirty = True
            log(f"  [摘要补齐] {len(summary)} chars")

    return authors, year, summary, dirty


def enrich_library(
    conn: Any,
    paper_ids: list[int] | None = None,
    *,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Backfill authors (OpenAlex/S2) and empty summaries for existing papers."""
    if paper_ids:
        qmarks = ",".join("?" * len(paper_ids))
        rows = conn.execute(
            f"SELECT id, title, authors, year, doi, abstract, summary, md_path "
            f"FROM papers WHERE id IN ({qmarks}) ORDER BY id",
            paper_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, authors, year, doi, abstract, summary, md_path "
            "FROM papers ORDER BY id"
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        pid = row["id"]
        authors = (row["authors"] or "").strip()
        summary = (row["summary"] or "").strip()
        year = row["year"]
        entry: dict[str, Any] = {"paper_id": pid, "title": row["title"], "status": "skip"}
        need_auth = force or not authors
        need_sum = force or not summary
        if not (need_auth or need_sum):
            out.append(entry)
            continue

        front = ""
        if row["md_path"] and Path(row["md_path"]).is_file():
            front = Path(row["md_path"]).read_text(encoding="utf-8", errors="replace")[:4000]

        authors2, year2, summary2, dirty = enrich_after_ingest(
            conn,
            paper_id=pid,
            title=row["title"] or "",
            # force=True 时清空作者，强制走 OpenAlex/S2 重查
            authors="" if force else authors,
            year=year,
            doi=row["doi"] or "",
            abstract=row["abstract"] or "",
            summary="" if force else summary,
            front=front or (row["abstract"] or ""),
            make_summary=need_sum or force,
            force_summary=force,
        )
        if dirty:
            entry["status"] = "updated"
            entry["authors"] = authors2
            entry["year"] = year2
            entry["summary_chars"] = len(summary2 or "")
            paper = db.get_paper(conn, pid)
            if paper:
                db.upsert_paper_fts(conn, pid, db.paper_index_text(paper))
        out.append(entry)
    return out


def ingest_dir(
    directory: str | Path, *, engine: str = "datalab", **kw: Any
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


def refresh_metadata(
    conn: Any, paper_ids: list[int] | None = None, *, force: bool = False
) -> list[dict[str, Any]]:
    """Re-parse title/authors from stored markdown (no re-embed).

    Only fills or extends weak fields unless force=True.
    """
    if paper_ids:
        qmarks = ",".join("?" * len(paper_ids))
        rows = conn.execute(
            f"SELECT id, title, authors, md_path FROM papers "
            f"WHERE id IN ({qmarks}) ORDER BY id",
            paper_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, title, authors, md_path FROM papers ORDER BY id"
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        pid = row["id"]
        old_title = (row["title"] or "").strip()
        old_auth = (row["authors"] or "").strip()
        md_path = row["md_path"] or ""
        entry: dict[str, Any] = {"paper_id": pid, "title": old_title, "status": "skip"}

        if not md_path or not Path(md_path).is_file():
            entry["status"] = "no_markdown"
            out.append(entry)
            continue

        front = Path(md_path).read_text(encoding="utf-8", errors="replace")[:4000]
        new_title = extract_title(front, {})
        new_auth = extract_authors(front, {})

        title_changed = False
        if new_title and new_title != "untitled":
            if force or len(new_title) > len(old_title) + 4 or old_title in ("", "untitled"):
                if new_title != old_title:
                    db.set_title(conn, pid, new_title)
                    entry["title"] = new_title
                    title_changed = True

        auth_changed = False
        if new_auth and (force or not old_auth or len(new_auth) > len(old_auth)):
            if new_auth != old_auth:
                db.set_authors(conn, pid, new_auth)
                entry["authors"] = new_auth
                auth_changed = True

        if title_changed or auth_changed:
            # keep stage-1 FTS in sync with the new title/authors
            paper = conn.execute("SELECT * FROM papers WHERE id = ?", (pid,)).fetchone()
            if paper:
                db.upsert_paper_fts(conn, pid, db.paper_index_text(paper))
            entry["status"] = "updated"
        out.append(entry)
    return out
