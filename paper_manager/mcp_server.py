# -*- coding: utf-8 -*-
"""FastMCP server exposing the paper library to any MCP client.

Run:  python -m paper_manager.mcp_server          (stdio)
      python -m paper_manager.mcp_server --http   (127.0.0.1:8820/mcp)

Agent-side .mcp.json entry (other-local-project mcp_bridge):
  "papers": {
    "command": "/path/to/python",
    "args": ["-m", "paper_manager.mcp_server"],
    "cwd": "/path/to/paper_manager"
  }
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from . import db, retriever
from .embedder import EmbeddingClient, RerankerClient
from .ingest import ingest_pdf

mcp = FastMCP(
    "paper-manager",
    instructions=(
        "本地论文库：PDF 导入后转为 Markdown 并建立混合检索"
        "（FTS5 + 向量 + 重排）。先用 search_papers 广度查找，"
        "再用 read_paper_section 深入阅读，节省 token。"
    ),
)

MAX_READ_CHARS = 6000


def _clients() -> tuple[EmbeddingClient | None, RerankerClient | None]:
    return EmbeddingClient.from_env(), RerankerClient.from_env()


@mcp.tool()
def search_papers(
    query: str,
    top_k: int = 5,
    year_min: int | None = None,
    year_max: int | None = None,
    author: str = "",
    venue: str = "",
) -> str:
    """按语义/关键词搜索本地论文库，可叠加元数据过滤。

    返回摘要级命中卡片（标题、年份、3-5 句摘要、最匹配片段及章节页码）。
    默认每篇论文只出一条最佳命中。想读全文某章节时用 read_paper_section。

    Args:
        query: 检索问题或关键词（中英文均可）。
        top_k: 返回论文数，默认 5。
        year_min: 发表年份下限（含），可选。
        year_max: 发表年份上限（含），可选。
        author: 作者名过滤（部分匹配），可选，如 "Vaswani"。
        venue: 期刊/会议过滤（部分匹配），可选，如 "NeurIPS"。
    """
    conn = db.connect()
    try:
        emb, rr = _clients()
        hits = retriever.search(
            conn,
            query,
            top_k=top_k,
            embedder=emb,
            reranker=rr,
            year_min=year_min,
            year_max=year_max,
            author=author or None,
            venue=venue or None,
        )
        return retriever.format_hits(hits)
    finally:
        conn.close()


@mcp.tool()
def read_paper_section(
    paper_id: int, section: str = "", max_chars: int = MAX_READ_CHARS
) -> str:
    """读取某篇论文的指定章节（或全文开头），分页返回。

    Args:
        paper_id: search_papers 返回的论文编号。
        section: 章节名（支持部分匹配，如 "method"、"3"；留空从头读全文）。
        max_chars: 本次返回的最大字符数，默认 6000。
    """
    conn = db.connect()
    try:
        paper = db.get_paper(conn, paper_id)
        if not paper:
            return f"paper_id={paper_id} 不存在"
        rows = db.read_section(conn, paper_id, section or None)
        if not rows:
            avail = [r["section"] for r in db.sections_of(conn, paper_id)]
            return f"未找到章节 “{section}”。可用章节：{'；'.join(avail[:30])}"
        parts = []
        for r in rows:
            head = f"[{r['section']}]"
            if r["page_start"]:
                head = f"[{r['section']} | p{r['page_start']}]"
            parts.append(head + "\n" + r["text"])
        text = "\n\n".join(parts)
        total = len(text)
        return (
            f"# {paper['title']}\n章节：{section or '全文'}｜总长 {total} 字符"
            + ("（已截断）" if total > max_chars else "")
            + "\n\n" + text[:max_chars]
        )
    finally:
        conn.close()


@mcp.tool()
def list_papers() -> str:
    """列出库中所有论文（编号、标题、作者、年份）。"""
    conn = db.connect()
    try:
        rows = db.list_papers(conn)
        if not rows:
            return "论文库为空。用 ingest_pdf 导入 PDF。"
        lines = [f"共 {len(rows)} 篇：", ""]
        for r in rows:
            year = f" ({r['year']})" if r["year"] else ""
            authors = (r["authors"] or "")[:40]
            lines.append(f"[{r['id']}] {r['title']}{year} — {authors}")
        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def ingest_pdf(path: str, engine: str = "datalab") -> str:
    """导入一个 PDF：转 Markdown、提取元数据、生成摘要、切块、嵌入入库。

    Args:
        path: PDF 的绝对路径。
        engine: "datalab"（默认，高保真，按页计费，多 key 自动轮询）或
                "local"（免费，纯文本抽取，适合简单文本型 PDF）。
    """
    report = ingest_pdf(path, engine=engine, embedder=EmbeddingClient.from_env())
    if report["status"] == "duplicate":
        return f"已存在（paper_id={report['paper_id']}）: {report['title']}"
    if report["status"] != "ok":
        return str(report)
    cost = report.get("cost_usd")
    cost_line = f"费用: ${cost:.4f}｜" if cost is not None else ""
    return (
        f"导入成功 paper_id={report['paper_id']}: {report['title']}\n"
        f"年份: {report.get('year')}｜DOI: {report.get('doi') or '未识别'}\n"
        f"{cost_line}切块: {report['chunks']}（向量化 {report['embedded']}）｜引擎: {report['engine']}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Paper manager MCP server")
    ap.add_argument("--http", action="store_true", help="streamable HTTP transport")
    ap.add_argument("--port", type=int, default=8820)
    args = ap.parse_args()
    if args.http:
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
