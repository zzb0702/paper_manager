# -*- coding: utf-8 -*-
"""Citation graph via OpenAlex (primary) with Semantic Scholar fallback.

OpenAlex is free without a key and generous (10 req/s with a polite
mailto). Flow per paper:

  1. resolve work      — DOI lookup, else normalized-title search
  2. references        — work.referenced_works (OpenAlex ids), batched
                         title lookups (50 per request)
  3. cited by          — filter=cites:{openalex_id}

Reference/citation rows store ext_id as ``doi:...`` when the work has a
DOI, else ``openalex:W...``; library-internal edges match on DOI first,
then on normalized title.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any

import requests

from . import db
from .util import log

OA_BASE = "https://api.openalex.org"
MAILTO = "you@example.com"  # polite-pool contact; replace for heavy use
S2_BASE = "https://api.semanticscholar.org/graph/v1"

SELECT = "id,doi,title,publication_year"


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (title or "").lower())


def _wid(url: str) -> str:
    return (url or "").rsplit("/", 1)[-1]


def _oa_get(path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    params = {**params, "mailto": MAILTO}
    for attempt in range(3):
        resp = requests.get(f"{OA_BASE}{path}", params=params, timeout=60, headers={
            "User-Agent": "paper-manager/0.1 (local research tool)"
        })
        if resp.status_code == 429:
            time.sleep(2 * (attempt + 1))
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("OpenAlex 持续限流（429）")


def _oa_work_by_doi(doi: str) -> dict[str, Any] | None:
    return _oa_get(f"/works/doi:{doi}", {"select": SELECT})


def _title_fuzzy(candidate: str, stored: str) -> bool:
    """Stored titles can be truncated (PDF line wraps); accept containment
    either way once both sides are normalized and long enough."""
    a, b = _norm(candidate), _norm(stored)
    if not a or not b:
        return False
    shorter = min(a, b)
    if len(shorter) < 12:
        return False
    return shorter in a and shorter in b


def _oa_work_by_title(title: str) -> dict[str, Any] | None:
    data = _oa_get(
        "/works",
        {"filter": f"title.search:{title[:150]}",
         "per-page": 5,
         "select": f"{SELECT},referenced_works"},
    )
    candidates = data.get("results", [])
    # prefer records that actually have a reference list indexed
    candidates.sort(key=lambda w: -len(w.get("referenced_works") or []))
    for w in candidates:
        if _title_fuzzy(w.get("title") or "", title):
            return w
    return None


def _resolve_work(title: str, doi: str) -> dict[str, Any] | None:
    if doi:
        try:
            w = _oa_work_by_doi(doi)
            if w:
                return w
        except Exception:
            pass
    return _oa_work_by_title(title)


def _rows_from_works(works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ext_id": (
                f"doi:{w['doi'].removeprefix('https://doi.org/')}"
                if w.get("doi") else f"openalex:{_wid(w.get('id', ''))}"
            ),
            "title": w.get("title") or "",
            "year": w.get("publication_year"),
        }
        for w in works
        if w.get("title")
    ]


def _oa_reference_titles(ref_ids: list[str]) -> list[dict[str, Any]]:
    works: list[dict[str, Any]] = []
    for i in range(0, min(len(ref_ids), 100), 50):
        batch = "|".join(ref_ids[i : i + 50])
        data = _oa_get(
            "/works",
            {"filter": f"ids.openalex:{batch}", "per-page": 50, "select": SELECT},
        )
        works += data.get("results", [])
    return works


def _fetch_openalex(title: str, doi: str) -> dict[str, Any] | None:
    work = _resolve_work(title, doi)
    if not work:
        return None
    wid = _wid(work.get("id", ""))
    # resolve 用 select 限定了字段；referenced_works 需要单独取
    full = _oa_get(f"/works/{wid}", {"select": "id,referenced_works"})
    ref_ids = [_wid(u) for u in (full or {}).get("referenced_works") or []]
    refs = _rows_from_works(_oa_reference_titles(ref_ids))
    cited_data = _oa_get(
        "/works",
        {"filter": f"cites:{wid}", "per-page": 100, "select": SELECT,
         # earliest citers first: the papers an early adopter (likely in a
         # personal library) cites are usually cited soon after publication
         "sort": "publication_date:asc"},
    )
    cited = _rows_from_works(cited_data.get("results", []))
    return {
        "refs": refs,
        "cited": cited,
        "s2_id": f"openalex:{wid}",
        "resolved_title": work.get("title") or "",
    }


# ------------------------------------------------ Semantic Scholar fallback

def _ext_id(ext: dict[str, Any] | None, title: str = "") -> str:
    ext = ext or {}
    if ext.get("DOI"):
        return f"doi:{ext['DOI']}"
    if ext.get("ArXiv"):
        return f"arxiv:{ext['ArXiv']}"
    if ext.get("CorpusId"):
        return f"corpus:{ext['CorpusId']}"
    # citations 表有 UNIQUE(paper_id,direction,ext_id)：无外部 ID 的行
    # 用标题哈希兜底，避免同方向的多条记录被 OR IGNORE 折叠成一条
    if title:
        return "th:" + hashlib.sha1(title.lower().encode()).hexdigest()[:16]
    return ""


def _s2_get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    headers = {"User-Agent": "paper-manager/0.1 (local research tool)"}
    # S2 免费申请的 API key 可避开共享池限流：https://www.semanticscholar.org/product/api
    api_key = os.getenv("S2_API_KEY", "").strip()
    if api_key:
        headers["x-api-key"] = api_key
    for attempt in range(4):
        resp = requests.get(url, params=params, timeout=60, headers=headers)
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            wait = 5 * (2 ** attempt)
            log(f"  [S2] 限流 429，等待 {wait}s 后重试")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Semantic Scholar 持续限流（429）")


def _fetch_s2(title: str, doi: str) -> dict[str, Any] | None:
    s2_id = None
    if doi:
        data = _s2_get(f"{S2_BASE}/paper/DOI:{doi}", {"fields": "title"})
        if data:
            s2_id = data["paperId"]
    if not s2_id:
        data = _s2_get(
            f"{S2_BASE}/paper/search",
            {"query": title[:150], "fields": "title", "limit": 5},
        )
        for item in (data or {}).get("data", []):
            if _title_fuzzy(item.get("title") or "", title):
                s2_id = item["paperId"]
                break
    if not s2_id:
        return None
    data = _s2_get(
        f"{S2_BASE}/paper/{s2_id}",
        {
            "fields": "title,year,externalIds,references.title,references.year,"
                      "references.externalIds,citations.title,citations.year,"
                      "citations.externalIds",
            "references.limit": 100,
            "citations.limit": 50,
        },
    )
    return {
        "refs": [
            {"ext_id": _ext_id(r.get("externalIds"), r.get("title") or ""), "title": r.get("title") or "",
             "year": r.get("year")}
            for r in data.get("references") or [] if r.get("title")
        ],
        "cited": [
            {"ext_id": _ext_id(r.get("externalIds"), r.get("title") or ""), "title": r.get("title") or "",
             "year": r.get("year")}
            for r in data.get("citations") or [] if r.get("title")
        ],
        "s2_id": s2_id,
        "resolved_title": data.get("title") or "",
    }


# ------------------------------------------------------------------ entry

def fetch_citations(conn, paper_id: int) -> dict[str, Any]:
    """Fetch and store the citation neighborhood of one library paper."""
    row = db.get_paper(conn, paper_id)
    if not row:
        raise ValueError(f"paper_id={paper_id} 不存在")
    title, doi = row["title"], row["doi"] or ""

    result = None
    source = "openalex"
    try:
        result = _fetch_openalex(title, doi)
    except Exception as exc:
        log(f"  [OpenAlex] 失败，转 Semantic Scholar: {str(exc)[:150]}")
    if result is None:
        source = "s2"
        result = _fetch_s2(title, doi)

    if result is None:
        # 不写 fetched_at：未收录的论文（如 OpenAlex 尚缺的 GraphRAG）
        # 保留在待抓取队列里，--all 下次运行会自动重试
        return {
            "paper_id": paper_id, "title": title, "status": "not_found",
            "refs": 0, "cited": 0, "source": source,
        }

    stored = db.upsert_citations(conn, paper_id, result["refs"], result["cited"])
    return {
        "paper_id": paper_id,
        "title": title,
        "status": "ok",
        "refs": len(result["refs"]),
        "cited": len(result["cited"]),
        "stored": stored,
        "source": source,
        "s2_id": result["s2_id"],
    }
