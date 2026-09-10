# -*- coding: utf-8 -*-
"""Library profile for agents: stats, research topics, representative papers.

Lightweight enough to call on every session start — no LLM required.
Topics come from concept-graph entities when present, with a title/abstract
keyword fallback so an empty KG still yields a usable direction summary.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from typing import Any

from . import db
from .util import format_authors

_STOP = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
    "via", "using", "from", "by", "at", "as", "is", "are", "be", "we",
    "our", "their", "this", "that", "these", "those", "towards", "toward",
    "how", "what", "when", "where", "which", "can", "do", "does", "not",
    "into", "over", "under", "based",
    "paper", "study", "approach", "method", "methods", "model", "models",
    "system", "systems", "framework", "frameworks",
}

_CJK = re.compile(r"[一-鿿]+")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+\-\.]{2,}")


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for m in _WORD.finditer(text or ""):
        w = m.group(0).strip(".-")
        if len(w) < 3 or w.lower() in _STOP:
            continue
        out.append(w)
    for seg in re.findall(r"[一-鿿]{2,8}", text or ""):
        if seg not in _STOP:
            out.append(seg)
    return out


def _kg_topics(conn: sqlite3.Connection, limit: int = 16) -> list[dict[str, Any]]:
    """Exact chunk↔entity links → topics ranked by paper coverage."""
    link_rows = conn.execute(
        "SELECT chunk_id, node_ids FROM kg_chunk_links"
    ).fetchall()
    chunk_paper = {
        r["id"]: r["paper_id"]
        for r in conn.execute("SELECT id, paper_id FROM chunks")
    }
    counts: Counter[int] = Counter()
    papers: dict[int, set[int]] = {}
    for lr in link_rows:
        try:
            nids = json.loads(lr["node_ids"])
        except Exception:
            continue
        pid = chunk_paper.get(lr["chunk_id"])
        for nid in nids:
            counts[int(nid)] += 1
            if pid is not None:
                papers.setdefault(int(nid), set()).add(int(pid))

    meta = {
        r["id"]: r
        for r in conn.execute("SELECT id, display, type FROM kg_nodes")
    }
    out: list[dict[str, Any]] = []
    for nid, n_chunks in counts.most_common(limit * 3):
        n = meta.get(nid)
        if not n:
            continue
        n_papers = len(papers.get(nid, ()))
        if n_papers <= 0:
            continue
        out.append({
            "name": n["display"],
            "type": n["type"],
            "n_chunks": n_chunks,
            "n_papers": n_papers,
        })
    out.sort(key=lambda t: (-t["n_papers"], -t["n_chunks"], t["name"]))
    return out[:limit]


def _text_topics(
    conn: sqlite3.Connection, limit: int = 12
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT title, abstract, summary FROM papers"
    ).fetchall()
    counter: Counter[str] = Counter()
    for r in rows:
        text = " ".join(
            filter(None, (r["title"] or "", r["abstract"] or "", r["summary"] or ""))
        )
        counter.update(_tokens(text))

    ranked = sorted(
        counter.items(),
        key=lambda kv: (-kv[1], -len(kv[0]), kv[0].lower()),
    )
    out: list[dict[str, Any]] = []
    seen_lower: set[str] = set()
    for term, n in ranked:
        low = term.lower()
        if low in seen_lower:
            continue
        # skip obvious substrings of already kept terms (e.g. "graphrag" vs longer form)
        if any(low in s for s in seen_lower if len(s) >= 4):
            continue
        seen_lower.add(low)
        out.append({"term": term, "n_papers": n})
        if len(out) >= limit:
            break
    return out


def _representatives(conn: sqlite3.Connection, limit: int = 8) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id, p.title, p.authors, p.year, p.summary,
               (SELECT COUNT(*) FROM chunks c WHERE c.paper_id = p.id) AS n_chunks
        FROM papers p
        ORDER BY
          CASE WHEN p.summary IS NOT NULL AND TRIM(p.summary) != '' THEN 1 ELSE 0 END DESC,
          n_chunks DESC,
          COALESCE(p.year, 0) DESC,
          p.id
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        summary = (r["summary"] or "").strip()
        out.append({
            "paper_id": r["id"],
            "title": r["title"],
            "year": r["year"],
            "authors": format_authors(r["authors"]),
            "n_chunks": r["n_chunks"],
            "summary": summary[:220],
        })
    return out


def _year_span(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT MIN(year) y0, MAX(year) y1, COUNT(*) c FROM papers "
        "WHERE year IS NOT NULL"
    ).fetchone()
    total = conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
    return {
        "total_papers": int(total),
        "year_min": row["y0"] if row and row["y0"] else None,
        "year_max": row["y1"] if row and row["y1"] else None,
        "with_year": int(row["c"]) if row else 0,
    }


def _gaps(conn: sqlite3.Connection) -> dict[str, int]:
    def _count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()["c"])

    return {
        "missing_authors": _count(
            "SELECT COUNT(*) c FROM papers WHERE authors IS NULL OR TRIM(authors)=''"
        ),
        "missing_summary": _count(
            "SELECT COUNT(*) c FROM papers WHERE summary IS NULL OR TRIM(summary)=''"
        ),
        "missing_year": _count(
            "SELECT COUNT(*) c FROM papers WHERE year IS NULL"
        ),
        "without_kg": _count(
            "SELECT COUNT(*) c FROM papers WHERE kg_built_at IS NULL"
        ),
        "without_citations": _count(
            "SELECT COUNT(*) c FROM papers WHERE citations_fetched_at IS NULL"
        ),
    }


def _user_topics(conn: sqlite3.Connection) -> dict[int, list[str]]:
    rows = conn.execute(
        "SELECT id, topics FROM papers "
        "WHERE topics IS NOT NULL AND TRIM(topics) != ''"
    ).fetchall()
    return {r["id"]: db.parse_topics(r["topics"]) for r in rows}


def build_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    stats = db.stats(conn)
    span = _year_span(conn)
    topics = _kg_topics(conn)
    text_topics = _text_topics(conn, limit=10)
    user_topics = _user_topics(conn)
    reps = _representatives(conn)
    recent = conn.execute(
        "SELECT id, title, year, added_at FROM papers "
        "ORDER BY added_at DESC, id DESC LIMIT 5"
    ).fetchall()
    tag_counter: Counter[str] = Counter()
    tag_display: dict[str, str] = {}
    for tags in user_topics.values():
        for t in tags:
            key = t.lower()
            tag_counter[key] += 1
            tag_display.setdefault(key, t)
    top_user = [
        {"tag": tag_display.get(t, t), "n_papers": n}
        for t, n in tag_counter.most_common(16)
    ]
    return {
        "stats": {
            "papers": stats["papers"],
            "chunks": stats["chunks"],
            "vectors": stats["vectors"],
            **span,
        },
        "topics": topics,
        "user_tags": top_user,
        "paper_tags": {str(k): v for k, v in user_topics.items()},
        "text_topics": text_topics,
        "representatives": reps,
        "recent": [dict(r) for r in recent],
        "gaps": _gaps(conn),
        "db_path": str(db.DB_PATH),
    }


def _direction_sentence(
    user_tags: list[dict[str, Any]],
    topics: list[dict[str, Any]],
    text_topics: list[dict[str, Any]],
) -> str:
    if user_tags:
        names = "、".join(t["tag"] for t in user_tags[:8])
        return f"人工/Agent 标注：{names}"
    if topics:
        names = "、".join(t["name"] for t in topics[:6])
        return f"概念图主题：{names}"
    if text_topics:
        names = "、".join(t["term"] for t in text_topics[:8])
        return f"高频主题词：{names}"
    return "主题信息不足：可 annotate 打标签，或 build-kg / 补摘要"


def format_overview(data: dict[str, Any]) -> str:
    s = data["stats"]
    lines: list[str] = ["# 本地论文库概览", ""]

    year = ""
    if s.get("year_min") and s.get("year_max"):
        year = f"｜年份 {s['year_min']}–{s['year_max']}"
    lines.append(
        f"**规模**：{s['papers']} 篇｜{s['chunks']} 章节块｜{s['vectors']} chunk 向量{year}"
    )
    lines.append(
        "**方向判断**："
        + _direction_sentence(
            data.get("user_tags") or [], data["topics"], data["text_topics"]
        )
    )
    lines.append("")

    if data.get("user_tags"):
        lines.append("## 人工/Agent 标注主题")
        lines.append(
            "、".join(f"{t['tag']}×{t['n_papers']}" for t in data["user_tags"][:16])
        )
        lines.append("")

    if data["topics"]:
        lines.append("## 概念主题（按覆盖论文数）")
        for t in data["topics"][:12]:
            lines.append(
                f"- {t['name']}（{t['type']}，{t['n_papers']} 篇 / {t['n_chunks']} 块）"
            )
        lines.append("")

    if data["text_topics"]:
        lines.append("## 标题/摘要高频词")
        lines.append(
            "、".join(f"{t['term']}×{t['n_papers']}" for t in data["text_topics"][:10])
        )
        lines.append("")

    if data["representatives"]:
        lines.append("## 代表论文（摘要完整优先）")
        paper_tags = data.get("paper_tags") or {}
        for p in data["representatives"]:
            year = f" ({p['year']})" if p["year"] else ""
            tags = paper_tags.get(str(p["paper_id"]))
            tag_s = ("  #" + ";".join(tags)) if tags else ""
            lines.append(
                f"- [{p['paper_id']}] {p['title']}{year}{tag_s}"
                + (f" — {p['authors']}" if p["authors"] else "")
            )
            if p["summary"]:
                lines.append(f"  {p['summary']}")
        lines.append("")

    if data["recent"]:
        lines.append("## 最近入库")
        for r in data["recent"]:
            year = f" ({r['year']})" if r["year"] else ""
            lines.append(f"- [{r['id']}] {r['title']}{year}")
        lines.append("")

    gaps = data["gaps"]
    lines.append("## 数据缺口")
    lines.append(
        "｜".join([
            f"缺作者 {gaps['missing_authors']}",
            f"缺摘要 {gaps['missing_summary']}",
            f"缺年份 {gaps['missing_year']}",
            f"未建概念图 {gaps['without_kg']}",
            f"未抓引文 {gaps['without_citations']}",
        ])
    )
    lines.append("")
    lines.append(
        "建议工作流：library_overview 了解方向 → search_papers 找相关 → "
        "read_paper_section 读证据。可用 annotate_paper 给论文补主题标签。"
    )
    return "\n".join(lines)


def overview_text(conn: sqlite3.Connection) -> str:
    return format_overview(build_overview(conn))
