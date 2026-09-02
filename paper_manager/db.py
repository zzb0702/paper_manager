# -*- coding: utf-8 -*-
"""SQLite storage: papers, chunks, FTS5 index, float32 vectors."""

from __future__ import annotations

import re
import sqlite3
import struct
from pathlib import Path
from typing import Any

from .config import DB_PATH, ensure_dirs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  authors TEXT DEFAULT '',
  year INTEGER,
  venue TEXT DEFAULT '',
  doi TEXT DEFAULT '',
  abstract TEXT DEFAULT '',
  summary TEXT DEFAULT '',
  pdf_path TEXT DEFAULT '',
  md_path TEXT DEFAULT '',
  sha256 TEXT NOT NULL UNIQUE,
  engine TEXT DEFAULT '',
  added_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  section TEXT DEFAULT '',
  page_start INTEGER,
  page_end INTEGER,
  text TEXT NOT NULL
);

-- trigram tokenizer keeps CJK substring search working
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='trigram');

CREATE TABLE IF NOT EXISTS chunk_vectors(
  chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL,
  dim INTEGER NOT NULL
);

-- paper-level stage-1 index: one FTS row per paper (rowid == paper id),
-- content = title / authors / abstract / summary
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(content, tokenize='trigram');

-- one vector per paper, embedding of title+abstract+summary
CREATE TABLE IF NOT EXISTS paper_vectors(
  paper_id INTEGER PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL,
  dim INTEGER NOT NULL
);

-- citation graph fetched from Semantic Scholar (P1)
-- direction 'refs': this paper cites the row; 'cited': the row cites this paper
CREATE TABLE IF NOT EXISTS citations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,
  ext_id TEXT DEFAULT '',
  title TEXT DEFAULT '',
  year INTEGER,
  UNIQUE(paper_id, direction, ext_id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
"""

_CITED_AT_COLUMN = "citations_fetched_at"


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(_SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight column migrations for databases created before P1."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
    if _CITED_AT_COLUMN not in cols:
        conn.execute(
            f"ALTER TABLE papers ADD COLUMN {_CITED_AT_COLUMN} TEXT"
        )
        conn.commit()


def vec_to_blob(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


# ---------------------------------------------------------------- papers

def find_by_sha(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM papers WHERE sha256 = ?", (sha256,)
    ).fetchone()


def insert_paper(
    conn: sqlite3.Connection, *, sha256: str, title: str, **fields: Any
) -> int:
    allowed = {
        "authors", "year", "venue", "doi", "abstract", "summary",
        "pdf_path", "md_path", "engine",
    }
    cols = ["title", "sha256"] + [c for c in fields if c in allowed]
    vals = [title, sha256] + [fields[c] for c in cols[2:]]
    sql = (
        f"INSERT INTO papers ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' * len(cols))})"
    )
    cur = conn.execute(sql, vals)
    conn.commit()
    return int(cur.lastrowid)


def get_paper(conn: sqlite3.Connection, paper_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()


def list_papers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, title, authors, year, added_at FROM papers ORDER BY id"
    ).fetchall()


def replace_chunks(
    conn: sqlite3.Connection,
    paper_id: int,
    chunks: list[dict[str, Any]],
    vectors: list[list[float] | None] | None,
) -> int:
    """(Re)write all chunks of a paper. FTS rows keep rowid == chunk id."""
    # FTS first: its subquery needs the chunk rows still present
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN "
        "(SELECT id FROM chunks WHERE paper_id = ?)", (paper_id,)
    )
    conn.execute("DELETE FROM chunks WHERE paper_id = ?", (paper_id,))
    for idx, ch in enumerate(chunks):
        cur = conn.execute(
            "INSERT INTO chunks (paper_id, ord, section, page_start, page_end, text)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (paper_id, idx, ch["section"], ch.get("page_start"), ch.get("page_end"), ch["text"]),
        )
        chunk_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)", (chunk_id, ch["text"])
        )
        vector = None if vectors is None else vectors[idx]
        if vector:
            conn.execute(
                "INSERT INTO chunk_vectors (chunk_id, embedding, dim) VALUES (?, ?, ?)",
                (chunk_id, vec_to_blob(vector), len(vector)),
            )
    conn.commit()
    return len(chunks)


# ------------------------------------------------------- paper-level index

def upsert_paper_index(
    conn: sqlite3.Connection,
    paper_id: int,
    text: str,
    vector: list[float] | None,
) -> None:
    """Refresh papers_fts (rowid == paper_id) and paper_vectors for a paper."""
    conn.execute("DELETE FROM papers_fts WHERE rowid = ?", (paper_id,))
    conn.execute(
        "INSERT INTO papers_fts (rowid, content) VALUES (?, ?)",
        (paper_id, text),
    )
    conn.execute("DELETE FROM paper_vectors WHERE paper_id = ?", (paper_id,))
    if vector:
        conn.execute(
            "INSERT INTO paper_vectors (paper_id, embedding, dim) VALUES (?, ?, ?)",
            (paper_id, vec_to_blob(vector), len(vector)),
        )
    conn.commit()


def delete_paper_index(conn: sqlite3.Connection, paper_id: int) -> None:
    conn.execute("DELETE FROM papers_fts WHERE rowid = ?", (paper_id,))
    conn.execute("DELETE FROM paper_vectors WHERE paper_id = ?", (paper_id,))


def delete_paper_fts(conn: sqlite3.Connection, paper_id: int) -> None:
    """Remove a paper's chunk FTS rows (chunks_fts has no FK to chunks)."""
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN "
        "(SELECT id FROM chunks WHERE paper_id = ?)",
        (paper_id,),
    )


def papers_missing_fts(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT p.id FROM papers p "
        "LEFT JOIN papers_fts f ON f.rowid = p.id WHERE f.rowid IS NULL"
    ).fetchall()
    return [r["id"] for r in rows]


def papers_missing_vectors(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT p.id, p.title, p.abstract, p.summary FROM papers p "
        "LEFT JOIN paper_vectors v ON v.paper_id = p.id WHERE v.paper_id IS NULL"
    ).fetchall()


def paper_index_text(row: sqlite3.Row) -> str:
    parts = [row["title"], "", row["abstract"], row["summary"]]
    return "\n".join(p or "" for p in parts).strip()


def search_papers_fts(
    conn: sqlite3.Connection, query: str, k: int = 30
) -> list[int]:
    """Paper ids ranked by papers_fts; LIKE fallback for short queries."""
    q = query.strip()
    if len(q) >= _FTS_MIN:
        try:
            rows = conn.execute(
                "SELECT rowid FROM papers_fts WHERE papers_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (_fts_quote(q), k),
            ).fetchall()
            if rows:
                return [r["rowid"] for r in rows]
        except sqlite3.OperationalError:
            pass
    like = f"%{q}%"
    rows = conn.execute(
        "SELECT p.id FROM papers p LEFT JOIN papers_fts f ON f.rowid = p.id "
        "WHERE f.content LIKE ? OR p.title LIKE ? LIMIT ?",
        (like, like, k),
    ).fetchall()
    return [r["id"] for r in rows]


# ------------------------------------------------------- citation graph

def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (title or "").lower())


def _title_match(a: str, b: str) -> bool:
    """Normalized containment both ways; guards against truncated titles
    from PDF line wraps."""
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    shorter = min(na, nb)
    if len(shorter) < 12:
        return False
    return shorter in na and shorter in nb


def upsert_citations(
    conn: sqlite3.Connection,
    paper_id: int,
    refs: list[dict[str, Any]],
    cited: list[dict[str, Any]],
) -> int:
    conn.execute("DELETE FROM citations WHERE paper_id = ?", (paper_id,))
    n = 0
    for direction, rows in (("refs", refs), ("cited", cited)):
        for r in rows:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO citations "
                    "(paper_id, direction, ext_id, title, year) VALUES (?, ?, ?, ?, ?)",
                    (paper_id, direction, str(r.get("ext_id") or ""),
                     r.get("title") or "", r.get("year")),
                )
                n += 1
            except sqlite3.IntegrityError:
                pass
    conn.execute(
        "UPDATE papers SET citations_fetched_at = datetime('now') WHERE id = ?",
        (paper_id,),
    )
    conn.commit()
    return n


def papers_without_citations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, title, doi FROM papers WHERE citations_fetched_at IS NULL "
        "ORDER BY id"
    ).fetchall()


def library_neighbors(conn: sqlite3.Connection, paper_id: int) -> dict[str, list]:
    """Citation neighbors of a paper that are themselves in the library."""
    paper_norms = {
        r["id"]: (_norm_title(r["title"]), (r["doi"] or "").lower().strip())
        for r in conn.execute("SELECT id, title, doi FROM papers")
    }
    out: dict[str, list[dict[str, Any]]] = {"cites": [], "cited_by": []}
    rows = conn.execute(
        "SELECT direction, ext_id, title, year FROM citations WHERE paper_id = ?",
        (paper_id,),
    ).fetchall()

    for r in rows:
        target = None
        ref_doi = (r["ext_id"] or "").lower()
        if ref_doi.startswith("doi:"):
            ref_doi = ref_doi[4:]
        for pid, (norm, doi) in paper_norms.items():
            if pid == paper_id:
                continue
            if ref_doi and doi and ref_doi == doi:
                target = pid
                break
            if r["title"] and _title_match(r["title"], norm):
                target = pid
                break
        if target is not None:
            out["cites" if r["direction"] == "refs" else "cited_by"].append(
                {"paper_id": target, "ext_title": r["title"], "year": r["year"]}
            )
    return out


def internal_citation_edges(conn: sqlite3.Connection) -> list[dict[str, int]]:
    """Edges between library papers, derived from BOTH stored directions:
    - src's refs contain dst        → src cites dst
    - src's cited_by contains dst   → dst cites src (edge dst→src)
    """
    paper_norms = {
        r["id"]: (_norm_title(r["title"]), (r["doi"] or "").lower().strip())
        for r in conn.execute("SELECT id, title, doi FROM papers")
    }
    by_doi = {doi: pid for pid, (_n, doi) in paper_norms.items() if doi}
    by_title = {norm: pid for pid, (norm, _d) in paper_norms.items() if norm}

    def match(ext: str, title: str) -> int | None:
        ext = (ext or "").lower()
        if ext.startswith("doi:"):
            ext = ext[4:]
        if ext and ext in by_doi:
            return by_doi[ext]
        if not title:
            return None
        exact = by_title.get(_norm_title(title))
        if exact:
            return exact
        for norm_candidate, pid in by_title.items():
            if _title_match(title, norm_candidate):
                return pid
        return None

    edges: dict[tuple[int, int], bool] = {}
    for r in conn.execute(
        "SELECT paper_id, direction, ext_id, title FROM citations"
    ).fetchall():
        other = match(r["ext_id"], r["title"])
        if not other:
            continue
        if r["direction"] == "refs":
            src, dst = r["paper_id"], other
        else:
            src, dst = other, r["paper_id"]
        if src != dst and (src, dst) not in edges:
            edges[(src, dst)] = True
    return [{"src": s, "dst": d} for (s, d) in edges]


# ---------------------------------------------------------------- search

_FTS_MIN = 3  # trigram tokenizer needs >= 3 chars


def _fts_quote(q: str) -> str:
    return '"' + q.replace('"', '""') + '"'


def search_fts(
    conn: sqlite3.Connection, query: str, k: int = 30,
    paper_ids: list[int] | None = None,
) -> list[int]:
    """Chunk ids ranked by FTS5; falls back to LIKE for short queries.

    Multi-word queries are split into quoted terms joined with OR — a
    trigram phrase match on the whole query would require the exact
    contiguous substring in the text. paper_ids restricts matching to
    chunks of those papers (stage 2).
    """
    q = query.strip()
    restrict = ""
    params: list[Any] = []
    if paper_ids is not None:
        if not paper_ids:
            return []
        marks = ",".join("?" * len(paper_ids))
        restrict = (
            f" AND rowid IN (SELECT id FROM chunks WHERE paper_id IN ({marks}))"
        )
        params = list(paper_ids)
    terms = [t for t in re.split(r"[\s,，;；、|]+", q) if len(t) >= _FTS_MIN]
    if terms:
        match_q = " OR ".join(_fts_quote(t) for t in terms)
        try:
            rows = conn.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?"
                f"{restrict} ORDER BY rank LIMIT ?",
                [match_q, *params, k],
            ).fetchall()
            if rows:
                return [r["rowid"] for r in rows]
        except sqlite3.OperationalError:
            pass
    like = f"%{q}%"
    if paper_ids is not None:
        marks = ",".join("?" * len(paper_ids))
        rows = conn.execute(
            f"SELECT id FROM chunks WHERE text LIKE ? AND paper_id IN ({marks})"
            " LIMIT ?",
            [like, *paper_ids, k],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id FROM chunks WHERE text LIKE ? LIMIT ?", (like, k)
        ).fetchall()
    return [r["id"] for r in rows]


def chunk_paper_map(
    conn: sqlite3.Connection, ids: list[int]
) -> dict[int, int]:
    """chunk_id -> paper_id for the given chunk ids."""
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, paper_id FROM chunks WHERE id IN ({marks})", ids
    ).fetchall()
    return {r["id"]: r["paper_id"] for r in rows}


def chunks_by_ids(conn: sqlite3.Connection, ids: list[int]) -> list[sqlite3.Row]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT c.*, p.title, p.year, p.summary AS paper_summary "
        f"FROM chunks c JOIN papers p ON p.id = c.paper_id "
        f"WHERE c.id IN ({marks})",
        ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def sections_of(conn: sqlite3.Connection, paper_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT DISTINCT section, MIN(page_start) AS p FROM chunks "
        "WHERE paper_id = ? GROUP BY section ORDER BY MIN(ord)",
        (paper_id,),
    ).fetchall()


def read_section(
    conn: sqlite3.Connection, paper_id: int, section: str | None
) -> list[sqlite3.Row]:
    if section:
        return conn.execute(
            "SELECT * FROM chunks WHERE paper_id = ? AND section LIKE ? ORDER BY ord",
            (paper_id, f"%{section}%"),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM chunks WHERE paper_id = ? ORDER BY ord", (paper_id,)
    ).fetchall()


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    papers = conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
    chunks = conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
    vecs = conn.execute("SELECT COUNT(*) c FROM chunk_vectors").fetchone()["c"]
    return {"papers": papers, "chunks": chunks, "vectors": vecs}


def sha256_of(path: str | Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>\]\)]+")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_ARXIV_RE = re.compile(r"arXiv[:\s]*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)


def extract_doi(text: str) -> str:
    m = _DOI_RE.search(text[:4000])
    if m:
        return m.group(0).rstrip(".,;)").strip()
    # arXiv preprints carry an automatic DOI (10.48550/arXiv.<id>) —
    # synthesizing it makes OpenAlex resolution work for them
    m = _ARXIV_RE.search(text[:4000])
    if m:
        return f"10.48550/arXiv.{m.group(1)}"
    return ""


def extract_year(text: str, meta: dict | None) -> int | None:
    """PDF meta date → arXiv id (reliable for preprints) → first year in
    the front text (weakest: may hit a cited year)."""
    m = _YEAR_RE.search((meta or {}).get("date") or "")
    if m:
        return int(m.group(0))
    m = _ARXIV_RE.search(text[:4000])
    if m:
        return 2000 + int(m.group(1)[:2])
    m = _YEAR_RE.search(text[:2000])
    return int(m.group(0)) if m else None
