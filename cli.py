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
import sys

from paper_manager import db, retriever
from paper_manager.embedder import EmbeddingClient, RerankerClient
from paper_manager.ingest import ingest_dir, ingest_pdf


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
        print(
            f"论文 {s['papers']} 篇｜chunks {s['chunks']}｜"
            f"向量化 {s['vectors']}｜库: {db.DB_PATH}"
        )
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="本地论文管理器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingest", help="导入单个 PDF")
    p.add_argument("pdf")
    p.add_argument("--engine", default="local", choices=["local", "datalab"])
    p.add_argument("--force", action="store_true", help="重复也重新导入")
    p.add_argument("--no-summary", action="store_true", help="跳过 LLM 摘要")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ingest-dir", help="批量导入目录下所有 PDF")
    p.add_argument("dir")
    p.add_argument("--engine", default="local", choices=["local", "datalab"])
    p.set_defaults(func=cmd_ingest_dir)

    p = sub.add_parser("search", help="混合检索")
    p.add_argument("query")
    p.add_argument("-k", "--top-k", type=int, default=5)
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

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
