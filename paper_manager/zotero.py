# -*- coding: utf-8 -*-
"""Local Zotero library reader (read-only, no extra deps).

Talks to Zotero's SQLite database so the agent can find items and local
attachment paths without installing zotero-mcp. Attachment files resolve
under the same data directory as the database (linked files keep their
absolute path from the `path` column).
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .util import log

# Common Zotero data-dir locations (Windows first, then cross-platform).
_WIN_CANDIDATES = (
    r"%USERPROFILE%\Zotero\zotero.sqlite",
    r"%USERPROFILE%\Zotero Dev Edition\zotero.sqlite",
    r"%USERPROFILE%\Zotero Beta\zotero.sqlite",
)
_POSIX_CANDIDATES = (
    "~/Zotero/zotero.sqlite",
    "~/Zotero Dev Edition/zotero.sqlite",
    "~/Zotero Beta/zotero.sqlite",
)

_ITEM_TYPE_KEEP = {
    "journalArticle",
    "conferencePaper",
    "preprint",
    "book",
    "bookSection",
    "report",
    "thesis",
    "manuscript",
    "document",
    "presentation",
}


def find_zotero_db() -> Path | None:
    """Resolve zotero.sqlite path from env or well-known locations."""
    env = os.getenv("ZOTERO_DB_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            p = p / "zotero.sqlite"
        return p if p.is_file() else None

    for raw in _WIN_CANDIDATES + _POSIX_CANDIDATES:
        p = Path(os.path.expandvars(raw)).expanduser()
        if p.is_file():
            return p
    return None


def _open(db_path: Path) -> sqlite3.Connection:
    # immutable=1: allow read while Zotero holds the write lock.
    uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.OperationalError:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _field_map(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT fieldID, fieldName FROM fields").fetchall()
    return {r["fieldName"]: r["fieldID"] for r in rows}


def _value_of(conn: sqlite3.Connection, item_id: int, field_id: int) -> str | None:
    row = conn.execute(
        """
        SELECT v.value FROM itemData d
        JOIN itemDataValues v ON v.valueID = d.valueID
        WHERE d.itemID = ? AND d.fieldID = ?
        """,
        (item_id, field_id),
    ).fetchone()
    return row["value"] if row else None


def _creators(conn: sqlite3.Connection, item_id: int) -> str:
    rows = conn.execute(
        """
        SELECT c.lastName, c.firstName, c.name
        FROM itemCreators ic
        JOIN creators c ON c.creatorID = ic.creatorID
        WHERE ic.itemID = ?
        ORDER BY ic.orderIndex
        """,
        (item_id,),
    ).fetchall()
    names = []
    for r in rows:
        if r["name"]:
            names.append(r["name"])
        elif r["lastName"] and r["firstName"]:
            names.append(f"{r['lastName']}, {r['firstName']}")
        elif r["lastName"]:
            names.append(r["lastName"])
    return "; ".join(names)


def _item_key(conn: sqlite3.Connection, item_id: int) -> str | None:
    row = conn.execute("SELECT key FROM items WHERE itemID = ?", (item_id,)).fetchone()
    return row["key"] if row else None


def _type_name(conn: sqlite3.Connection, type_id: int) -> str:
    row = conn.execute("SELECT typeName FROM itemTypes WHERE itemTypeID = ?", (type_id,)).fetchone()
    return row["typeName"] if row else "item"


def _attachment_paths(conn: sqlite3.Connection, parent_id: int, data_dir: Path) -> list[str]:
    """Local paths for PDF (and other file) attachments of one parent item."""
    rows = conn.execute(
        """
        SELECT i.itemID, i.key, a.path, a.contentType, a.linkMode
        FROM itemAttachments a
        JOIN items i ON i.itemID = a.itemID
        WHERE a.parentItemID = ?
          AND i.itemID NOT IN (SELECT itemID FROM deletedItems)
        """,
        (parent_id,),
    ).fetchall()

    paths: list[str] = []
    for r in rows:
        content_type = (r["contentType"] or "").lower()
        raw = r["path"] or ""
        candidates: list[Path] = []

        if raw.startswith("storage:"):
            # storage:relative/path.pdf → {data_dir}/storage/{item.key}/...
            rel = raw[len("storage:") :]
            candidates.append(data_dir / "storage" / r["key"] / Path(rel).name)
            # Some libs nest; also try storage/{key}/{rel}
            candidates.append(data_dir / "storage" / r["key"] / rel)
        elif raw and not raw.startswith("http") and not raw.startswith("attachment:"):
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = data_dir / p
            candidates.append(p)
        else:
            # No path in DB: fall back to storage folder for this attachment key.
            storage = data_dir / "storage" / r["key"]
            if storage.is_dir():
                candidates.extend(sorted(storage.glob("*.pdf")))

        seen: set[str] = set()
        for c in candidates:
            try:
                resolved = c.resolve()
            except OSError:
                continue
            s = str(resolved)
            if s in seen:
                continue
            seen.add(s)
            if resolved.is_file():
                # Prefer PDFs; still accept any real file if content-type says pdf.
                if resolved.suffix.lower() == ".pdf" or content_type == "application/pdf":
                    paths.append(s)
    return paths


def _parse_year(date_val: str | None) -> int | None:
    if not date_val:
        return None
    m = re.search(r"(1[5-9]\d{2}|20\d{2})", date_val)
    return int(m.group(1)) if m else None


def _row_to_item(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    fields: dict[str, int],
    data_dir: Path,
    *,
    with_paths: bool,
) -> dict[str, Any]:
    item_id = row["itemID"]
    title = _value_of(conn, item_id, fields.get("title", -1)) or "(无标题)"
    year = _parse_year(
        _value_of(conn, item_id, fields.get("date", -1))
        or _value_of(conn, item_id, fields.get("year", -1))
    )
    item: dict[str, Any] = {
        "key": _item_key(conn, item_id),
        "item_id": item_id,
        "title": title,
        "authors": _creators(conn, item_id),
        "year": year,
        "url": _value_of(conn, item_id, fields.get("url", -1)),
        "doi": _value_of(conn, item_id, fields.get("DOI", -1)),
        "publication": _value_of(conn, item_id, fields.get("publicationTitle", -1))
        or _value_of(conn, item_id, fields.get("proceedingsTitle", -1)),
        "item_type": _type_name(conn, row["itemTypeID"]),
        "pdf_paths": _attachment_paths(conn, item_id, data_dir) if with_paths else [],
    }
    return item


def status() -> dict[str, Any]:
    db_path = find_zotero_db()
    if not db_path:
        return {
            "ok": False,
            "db_path": None,
            "hint": "未找到 zotero.sqlite。设置 ZOTERO_DB_PATH 或把 Zotero 数据目录放在默认位置。",
        }
    try:
        conn = _open(db_path)
        try:
            n = conn.execute(
                """
                SELECT COUNT(*) c FROM items i
                WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
                  AND i.itemTypeID IN (SELECT itemTypeID FROM itemTypes)
                """
            ).fetchone()["c"]
        finally:
            conn.close()
        return {"ok": True, "db_path": str(db_path), "data_dir": str(db_path.parent), "items": n}
    except Exception as exc:  # noqa: BLE001 — surface any open failure to the caller
        return {"ok": False, "db_path": str(db_path), "error": f"{type(exc).__name__}: {exc}"}


def search(
    query: str,
    *,
    limit: int = 10,
    item_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Keyword search over title / creators / publication / DOI.

    Returns items with local PDF paths (when attachments resolve).
    """
    db_path = find_zotero_db()
    if not db_path:
        raise FileNotFoundError(
            "未找到 Zotero 数据库。请启动 Zotero，或设置 ZOTERO_DB_PATH=/path/to/zotero.sqlite"
        )

    q = query.strip()
    if not q:
        return []

    conn = _open(db_path)
    try:
        fields = _field_map(conn)
        data_dir = db_path.parent
        title_f = fields.get("title", -1)
        date_f = fields.get("date", -1)
        pub_f = fields.get("publicationTitle", -1)
        proc_f = fields.get("proceedingsTitle", -1)
        doi_f = fields.get("DOI", -1)
        url_f = fields.get("url", -1)

        # Broad SQL filter via FTS-less LIKE, then refine in Python if needed.
        like = f"%{q}%"
        type_filter = ""
        params: list[Any] = []
        keep_types = item_types or []
        if keep_types:
            placeholders = ",".join("?" * len(keep_types))
            type_filter = f" AND t.typeName IN ({placeholders})"
            params.extend(keep_types)

        rows = conn.execute(
            f"""
            SELECT DISTINCT i.itemID, i.itemTypeID
            FROM items i
            JOIN itemTypes t ON t.itemTypeID = i.itemTypeID
            LEFT JOIN itemData d ON d.itemID = i.itemID AND d.fieldID IN (?,?,?,?,?)
            LEFT JOIN itemDataValues v ON v.valueID = d.valueID
            WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)
              AND t.typeName IN (
                'journalArticle','conferencePaper','preprint','book','bookSection',
                'report','thesis','manuscript','document'
              )
              {type_filter}
              AND (
                v.value LIKE ?
                OR i.key = ?
                OR i.itemID IN (
                  SELECT ic.itemID FROM itemCreators ic
                  JOIN creators c ON c.creatorID = ic.creatorID
                  WHERE c.lastName LIKE ? OR c.firstName LIKE ? OR c.name LIKE ?
                )
              )
            """,
            [
                title_f,
                date_f,
                pub_f,
                proc_f,
                doi_f,
                *params,
                like,
                q,
                like,
                like,
                like,
            ],
        ).fetchall()

        # Rank: title match first, then creators, then others.
        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            item = _row_to_item(conn, row, fields, data_dir, with_paths=True)
            if keep_types and item["item_type"] not in keep_types:
                continue
            title_hit = q.lower() in (item["title"] or "").lower()
            author_hit = q.lower() in (item["authors"] or "").lower()
            score = 0 if title_hit else 1 if author_hit else 2
            scored.append((score, item))

        scored.sort(key=lambda x: x[0])
        return [item for _, item in scored[:limit]]
    finally:
        conn.close()


def get_item(key_or_id: str) -> dict[str, Any]:
    """Fetch one item by Zotero key (8-char) or numeric itemID."""
    db_path = find_zotero_db()
    if not db_path:
        raise FileNotFoundError(
            "未找到 Zotero 数据库。请启动 Zotero，或设置 ZOTERO_DB_PATH=/path/to/zotero.sqlite"
        )

    conn = _open(db_path)
    try:
        fields = _field_map(conn)
        data_dir = db_path.parent
        row = None
        if key_or_id.strip().isdigit():
            row = conn.execute(
                """
                SELECT itemID, itemTypeID FROM items
                WHERE itemID = ? AND itemID NOT IN (SELECT itemID FROM deletedItems)
                """,
                (int(key_or_id),),
            ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT itemID, itemTypeID FROM items
                WHERE key = ? COLLATE NOCASE
                  AND itemID NOT IN (SELECT itemID FROM deletedItems)
                """,
                (key_or_id.strip(),),
            ).fetchone()
        if row is None:
            return {"error": f"Zotero item 未找到: {key_or_id}"}
        return _row_to_item(conn, row, fields, data_dir, with_paths=True)
    finally:
        conn.close()
