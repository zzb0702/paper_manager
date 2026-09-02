# -*- coding: utf-8 -*-
"""Paper manager CLI.

用法:
    python cli.py ingest paper.pdf [--engine local|datalab] [--force] [--no-summary]
    python cli.py ingest-dir D:\\papers [--engine datalab]
    python cli.py search "attention mechanism" [-k 5]
    python cli.py read 3 --section method
    python cli.py list
    python cli.py status
"""

from __future__ import annotations

import argparse
import os
import sys

from paper_manager import db, retriever
from paper_manager.embedder import EmbeddingClient, RerankerClient
from paper_manager.ingest import ingest_dir, ingest_pdf

_REWRITE_CACHE: dict[str, list[str] | None] = {}


def _rewriter() -> retriever.QueryRewriter | None:
    """LLM query rewriter with per-process caching; None when no LLM env."""
    if not os.getenv("LLM_BASE_URL", "").strip():
        return None
    from paper_manager.llm import rewrite_query

    def _rewrite(q: str) -> list[str] | None:
        if q not in _REWRITE_CACHE:
            _REWRITE_CACHE[q] = rewrite_query(q)
        return _REWRITE_CACHE[q]

    return _rewrite


def cmd_ingest(args: argparse.Namespace) -> None:
    report = ingest_pdf(
        args.pdf,
        engine=args.engine,
        force=args.force,
        embedder=EmbeddingClient.from_env(),
        make_summary=not args.no_summary,
    )
    print(report)


def cmd_ingest_dir(args: argparse.Namespace) -> None:
    reports = ingest_dir(args.dir, engine=args.engine)
    ok = sum(1 for r in reports if r.get("status") == "ok")
    dup = sum(1 for r in reports if r.get("status") == "duplicate")
    err = sum(1 for r in reports if r.get("status") == "error")
    print(f"\n完成: 新增 {ok}，重复 {dup}，失败 {err}")


def cmd_search(args: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        hits = retriever.search(
            conn,
            args.query,
            top_k=args.top_k,
            embedder=EmbeddingClient.from_env(),
            reranker=RerankerClient.from_env(),
            year_min=args.year_min,
            year_max=args.year_max,
            author=args.author,
            venue=args.venue,
            query_rewriter=_rewriter(),
        )
        print(retriever.format_hits(hits))
    finally:
        conn.close()


def cmd_read(args: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        paper = db.get_paper(conn, args.paper_id)
        if not paper:
            print(f"paper_id={args.paper_id} 不存在")
            sys.exit(1)
        rows = db.read_section(conn, args.paper_id, args.section or None)
        if not rows:
            avail = [r["section"] for r in db.sections_of(conn, args.paper_id)]
            print(f"未找到章节。可用: {'；'.join(avail[:40])}")
            sys.exit(1)
        print(f"# [{paper['id']}] {paper['title']}")
        if paper["summary"]:
            print(f"摘要卡：{paper['summary']}\n")
        text = "\n\n".join(
            f"[{r['section']}]" + (f"(p{r['page_start']})" if r["page_start"] else "") + f"\n{r['text']}"
            for r in rows
        )
        print(text[: args.max_chars])
        if len(text) > args.max_chars:
            print(f"\n…（已截断，共 {len(text)} 字符，--max-chars 调整）")
    finally:
        conn.close()


def cmd_fetch_citations(args: argparse.Namespace) -> None:
    from paper_manager import scholar

    conn = db.connect()
    try:
        if args.paper_id:
            rows = [db.get_paper(conn, args.paper_id)]
            rows = [r for r in rows if r]
        else:
            rows = db.papers_without_citations(conn)
        if not rows:
            print("没有待抓取的论文（全部已有引文数据）")
            return
        import time as _time

        for i, row in enumerate(rows):
            try:
                r = scholar.fetch_citations(conn, row["id"])
                print(
                    f"[{r['status']}] {r['title'][:50]} — "
                    f"参考文献 {r['refs']}，被引 {r['cited']}"
                )
            except Exception as exc:
                print(f"[error] {row['title'][:50]}: {type(exc).__name__}: {exc}")
            if i < len(rows) - 1:
                _time.sleep(1.5)  # S2 unauthenticated pool: ~100 req / 5 min
    finally:
        conn.close()


def cmd_related(args: argparse.Namespace) -> None:
    from paper_manager.retriever import related_papers

    conn = db.connect()
    try:
        r = related_papers(
            conn, args.paper_id, top_k=args.top_k,
            embedder=EmbeddingClient.from_env(),
        )
        print(f"# [{r['paper_id']}] {r['title']}\n")
        for key, label in (("cites", "引用（库内）"), ("cited_by", "被引（库内）")):
            if r[key]:
                print(f"## {label}")
                for c in r[key]:
                    year = f" ({c['year']})" if c["year"] else ""
                    print(f"  [{c['paper_id']}] {c['title']}{year}")
        if r["semantic"]:
            print("## 语义相近")
            for c in r["semantic"]:
                year = f" ({c['year']})" if c["year"] else ""
                print(f"  [{c['paper_id']}] {c['title']}{year} — 相似度 {c['similarity']}")
        if not (r["cites"] or r["cited_by"] or r["semantic"]):
            print("暂无邻居：先 python cli.py fetch-citations 抓取引文")
    finally:
        conn.close()


def cmd_build_kg(args: argparse.Namespace) -> None:
    from paper_manager import kg

    conn = db.connect()
    try:
        if args.paper_id:
            rows = [r for r in [db.get_paper(conn, args.paper_id)] if r]
        else:
            rows = db.papers_without_kg(conn)
        if not rows:
            print("没有待构建的论文（概念图已全部构建）")
            return
        for row in rows:
            try:
                r = kg.build_paper_kg(conn, row["id"])
                print(
                    f"[{r['status']}] {r['title'][:50]} — "
                    f"实体 {r.get('entities', 0)}，关系 {r.get('relations', 0)}"
                    f"，关联块 {r.get('chunks_linked', 0)}"
                )
            except Exception as exc:
                print(f"[error] {row['title'][:50]}: {type(exc).__name__}: {exc}")
    finally:
        conn.close()


def cmd_kg_search(args: argparse.Namespace) -> None:
    from paper_manager import kg

    conn = db.connect()
    try:
        res = kg.search_graph(
            conn, args.query, top_k=args.top_k,
            embedder=EmbeddingClient.from_env(),
        )
        print(kg.format_graph_result(res, args.query))
    finally:
        conn.close()


def cmd_list(_: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        for r in db.list_papers(conn):
            year = f" ({r['year']})" if r["year"] else ""
            print(f"[{r['id']}] {r['title']}{year} — {(r['authors'] or '')[:60]}")
    finally:
        conn.close()


def cmd_status(_: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        s = db.stats(conn)
        n_vec = conn.execute("SELECT COUNT(*) c FROM paper_vectors").fetchone()["c"]
        print(
            f"论文 {s['papers']} 篇｜chunks {s['chunks']}｜"
            f"chunk 向量 {s['vectors']}｜论文向量 {n_vec}｜库: {db.DB_PATH}"
        )
    finally:
        conn.close()


def cmd_backfill(_: argparse.Namespace) -> None:
    """Build stage-1 paper index (FTS rows + paper vectors) for legacy rows."""
    from paper_manager.retriever import _backfill_paper_index

    conn = db.connect()
    try:
        _backfill_paper_index(conn, EmbeddingClient.from_env())
        n_vec = conn.execute(
            "SELECT COUNT(*) c FROM paper_vectors"
        ).fetchone()["c"]
        print(f"补齐完成，论文向量共 {n_vec} 条")
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="本地论文管理器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="导入单个 PDF（默认 datalab，按页计费）")
    p.add_argument("pdf")
    p.add_argument("--engine", default="datalab", choices=["datalab", "local"],
                   help="datalab=高保真按页计费（默认）；local=免费纯文本抽取")
    p.add_argument("--force", action="store_true", help="重复也重新导入")
    p.add_argument("--no-summary", action="store_true", help="跳过 LLM 摘要")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ingest-dir", help="批量导入目录下所有 PDF")
    p.add_argument("dir")
    p.add_argument("--engine", default="datalab", choices=["datalab", "local"])
    p.set_defaults(func=cmd_ingest_dir)

    p = sub.add_parser("search", help="混合检索（可按年份/作者/期刊过滤）")
    p.add_argument("query")
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.add_argument("--year-min", type=int, default=None, help="年份下限（含）")
    p.add_argument("--year-max", type=int, default=None, help="年份上限（含）")
    p.add_argument("--author", default=None, help="作者过滤（部分匹配）")
    p.add_argument("--venue", default=None, help="期刊/会议过滤（部分匹配）")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("read", help="读论文章节")
    p.add_argument("paper_id", type=int)
    p.add_argument("--section", default="")
    p.add_argument("--max-chars", type=int, default=6000)
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("list", help="列出所有论文")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("status", help="库统计")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("backfill", help="补齐存量论文的论文级索引（FTS+向量）")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("fetch-citations", help="从 Semantic Scholar 抓取引文关系")
    p.add_argument("paper_id", type=int, nargs="?", default=None)
    p.add_argument("--all", action="store_true", help="抓取所有未抓取的论文")
    p.set_defaults(func=cmd_fetch_citations)

    p = sub.add_parser("related", help="某篇论文的邻居（引文+语义）")
    p.add_argument("paper_id", type=int)
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.set_defaults(func=cmd_related)

    p = sub.add_parser("build-kg", help="LLM 抽取实体/关系构建概念图")
    p.add_argument("paper_id", type=int, nargs="?", default=None)
    p.add_argument("--all", action="store_true", help="构建所有未构建的论文")
    p.set_defaults(func=cmd_build_kg)

    p = sub.add_parser("kg", help="概念图检索（双层：实体→邻域→章节）")
    p.add_argument("query")
    p.add_argument("-k", "--top-k", type=int, default=5)
    p.set_defaults(func=cmd_kg_search)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
