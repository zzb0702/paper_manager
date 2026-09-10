# -*- coding: utf-8 -*-
"""FastMCP server exposing the paper library to any MCP client.

Run:  python -m paper_manager.mcp_server          (stdio)
      python -m paper_manager.mcp_server --http   (127.0.0.1:8820/mcp)

Agent-side .mcp.json entry (use an absolute interpreter path and the repo root):
  "papers": {
    "command": "/absolute/path/to/python",
    "args": ["-m", "paper_manager.mcp_server"],
    "cwd": "/absolute/path/to/paper_manager"
  }
"""

from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from . import db, retriever
from .embedder import EmbeddingClient, RerankerClient
from .ingest import ingest_pdf as _ingest_pdf
from .util import format_authors

mcp = FastMCP(
    "paper-manager",
    instructions=(
        "本地论文库：PDF 导入后转为 Markdown 并建立混合检索"
        "（FTS5 + 向量 + 重排）。会话开始先调 library_overview "
        "了解研究方向与库存；再用 search_papers 广度查找，"
        "最后用 read_paper_section 深入阅读，节省 token。"
        "可用 annotate_paper 为论文打主题标签/笔记，便于后续检索。"
        "可选 Zotero 集成：search_zotero 找本地文献 → ingest_from_zotero "
        "导入精读索引，无需再单独安装 zotero-mcp。"
    ),
)

MAX_READ_CHARS = 6000


def _clients() -> tuple[EmbeddingClient | None, RerankerClient | None]:
    return EmbeddingClient.from_env(), RerankerClient.from_env()


_REWRITE_CACHE: dict[str, list[str] | None] = {}


def _rewriter() -> retriever.QueryRewriter | None:
    """LLM query rewriter (cached per process); None when no LLM env."""
    import os

    if not os.getenv("LLM_BASE_URL", "").strip():
        return None
    from .llm import rewrite_query

    def _rewrite(q: str) -> list[str] | None:
        if q not in _REWRITE_CACHE:
            _REWRITE_CACHE[q] = rewrite_query(q)
        return _REWRITE_CACHE[q]

    return _rewrite


@mcp.tool()
def library_overview() -> str:
    """查看本地论文库画像：规模、研究方向、代表论文、数据缺口。

    会话开始或不确定库里有什么时先调用本工具。无需 LLM，纯本地聚合：
    人工/Agent 标注标签（优先）→ 概念图实体 → 标题/摘要高频词。
    返回 Markdown 概览，含建议 paper_id，便于后续 search_papers /
    read_paper_section / annotate_paper。
    """
    from .overview import overview_text

    conn = db.connect()
    try:
        if conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"] == 0:
            return (
                "论文库为空。\n"
                "用 ingest_pdf 导入 PDF，或 ingest_from_zotero 从 Zotero 拉取。"
            )
        return overview_text(conn)
    finally:
        conn.close()


@mcp.tool()
def annotate_paper(
    paper_id: int,
    topics: list[str] | None = None,
    notes: str = "",
    replace_topics: bool = False,
) -> str:
    """给论文写入主题标签（topics）与可选笔记（notes），供检索与 overview 使用。

    默认 merge：在已有标签上追加去重。replace_topics=True 时整表覆盖 topics。
    notes 传入则覆盖笔记（空字符串可清空）。写入后自动刷新论文级 FTS。

    Args:
        paper_id: 论文编号。
        topics: 主题标签列表，如 ["GraphRAG", "方法对比", "精读"]。
        notes: 自由笔记（可选），供后续 Agent 回忆。
        replace_topics: True 时用 topics 整体替换已有标签（默认 False=合并）。
    """
    conn = db.connect()
    try:
        paper = db.get_paper(conn, paper_id)
        if not paper:
            return f"paper_id={paper_id} 不存在"
        topic_list = topics or []
        notes_arg = notes if notes else None
        # allow notes-only updates
        if not topic_list and notes_arg is None:
            return (
                f"[{paper_id}] {paper['title']}\n"
                f"当前 topics: {paper['topics'] or '（无）'}\n"
                f"当前 notes: {(paper['notes'] or '（无）')[:200]}"
            )
        saved = db.set_paper_topics(
            conn,
            paper_id,
            topic_list,
            merge=not replace_topics,
            notes=notes_arg,
        )
        fresh = db.get_paper(conn, paper_id)
        return (
            f"已更新 [{paper_id}] {fresh['title'] if fresh else paper['title']}\n"
            f"topics: {'; '.join(saved) or '（空）'}\n"
            f"notes: {((fresh['notes'] if fresh else notes) or '（空）')[:200]}"
        )
    finally:
        conn.close()


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

    检索为两阶段：先在论文级（摘要卡+摘要向量）召回候选论文，
    再在候选论文的章节块内做混合检索与聚合排序；问题会先由 LLM
    改写成 2-3 组中英文检索关键词以提升召回。返回摘要级命中卡片
    （标题、年份、主题标签、摘要、最匹配片段及章节页码、相关段落计数）。

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
            query_rewriter=_rewriter(),
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
    """列出库中所有论文（编号、标题、作者、年份、主题标签）。

    适合快速扫库存；方向画像请用 library_overview。
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, authors, year, topics, summary FROM papers ORDER BY id"
        ).fetchall()
        if not rows:
            return "论文库为空。用 ingest_pdf 导入 PDF。"
        lines = [f"共 {len(rows)} 篇：", ""]
        for r in rows:
            year = f" ({r['year']})" if r["year"] else ""
            authors = format_authors(r["authors"])
            tags = f"  #{r['topics']}" if r["topics"] else ""
            lines.append(f"[{r['id']}] {r['title']}{year}{tags} — {authors}")
            summary = (r["summary"] or "").strip()
            if summary:
                lines.append(f"  {summary[:160]}")
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
    report = _ingest_pdf(path, engine=engine, embedder=EmbeddingClient.from_env())
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


@mcp.tool()
def related_papers(paper_id: int, top_k: int = 5) -> str:
    """查看某篇论文的邻居：引文关系（库内引用/被引）+ 语义相近论文。

    用于顺着引用链追溯方法源头、找后续工作、或发现同主题论文。
    引文数据需要先用 ingest 侧的 fetch-citations（CLI）抓取过才完整；
    语义近邻基于论文向量，始终可用。

    Args:
        paper_id: 论文编号（search_papers / list_papers 返回）。
        top_k: 语义近邻数量，默认 5。
    """
    from .retriever import related_papers as _related

    conn = db.connect()
    try:
        r = _related(conn, paper_id, top_k=top_k, embedder=EmbeddingClient.from_env())
    except ValueError:
        return f"paper_id={paper_id} 不存在"
    finally:
        conn.close()

    lines = [f"# [{r['paper_id']}] {r['title']}", ""]
    if r["cites"]:
        lines.append("## 引用（库内，方法/背景来源）")
        lines += [f"- [{c['paper_id']}] {c['title']} ({c.get('year') or '?'})" for c in r["cites"]]
    if r["cited_by"]:
        lines.append("## 被引（库内，后续工作）")
        lines += [f"- [{c['paper_id']}] {c['title']} ({c.get('year') or '?'})" for c in r["cited_by"]]
    if r["semantic"]:
        lines.append("## 语义相近")
        lines += [
            f"- [{c['paper_id']}] {c['title']} ({c.get('year') or '?'}，相似度 {c['similarity']})"
            for c in r["semantic"]
        ]
    if len(lines) == 2:
        return f"[{paper_id}] {r['title']}：暂无库内邻居。引文数据可由 CLI fetch-citations 抓取。"
    return "\n".join(lines)


@mcp.tool()
def search_zotero(query: str, limit: int = 8) -> str:
    """在本机 Zotero 文献库中按标题/作者/DOI/会议搜索条目，并尽量解析本地 PDF 路径。

    与 search_papers 的区别：search_zotero 找的是 Zotero 藏书（元数据+附件路径）；
    search_papers 搜的是 paper_manager 精读库（已导入并切块的正文）。
    典型流程：search_zotero → ingest_from_zotero → search_papers / read_paper_section。

    Args:
        query: 标题关键词、作者姓、DOI 或 Zotero key。
        limit: 最多返回条数，默认 8。
    """
    from . import zotero

    try:
        items = zotero.search(query, limit=limit)
    except FileNotFoundError as exc:
        return (
            f"{exc}\n"
            "提示：在 Zotero 中启用本机 API（设置→高级），并把数据目录保持默认；"
            "若路径特殊，设置环境变量 ZOTERO_DB_PATH 指向 zotero.sqlite。"
        )
    except Exception as exc:  # noqa: BLE001
        return f"Zotero 搜索失败: {type(exc).__name__}: {exc}"

    if not items:
        return f"Zotero 中未找到与 “{query}” 匹配的条目。"

    lines = [f"Zotero 命中 {len(items)} 条：", ""]
    for it in items:
        year = f" ({it['year']})" if it.get("year") else ""
        authors = (it.get("authors") or "")[:60]
        lines.append(f"[{it['key']}] {it['title']}{year}")
        if authors:
            lines.append(f"  作者: {authors}")
        if it.get("doi"):
            lines.append(f"  DOI: {it['doi']}")
        if it.get("publication"):
            lines.append(f"  来源: {it['publication']}")
        paths = it.get("pdf_paths") or []
        if paths:
            lines.append(f"  PDF: {paths[0]}")
        else:
            lines.append("  PDF: 未解析到本地附件（可用 ingest_pdf 传入绝对路径）")
        lines.append("")
    return "\n".join(lines).rstrip()


@mcp.tool()
def get_zotero_item(key: str) -> str:
    """读取单条 Zotero 记录的元数据与本地 PDF 路径。

    Args:
        key: Zotero item key（8 位字母数字）或数字 itemID；来自 search_zotero。
    """
    from . import zotero

    try:
        item = zotero.get_item(key)
    except Exception as exc:  # noqa: BLE001
        return f"读取 Zotero 条目失败: {type(exc).__name__}: {exc}"

    if "error" in item:
        return str(item["error"])

    year = f" ({item['year']})" if item.get("year") else ""
    lines = [
        f"[{item['key']}] {item['title']}{year}",
        f"类型: {item.get('item_type')}",
    ]
    if item.get("authors"):
        lines.append(f"作者: {item['authors']}")
    if item.get("doi"):
        lines.append(f"DOI: {item['doi']}")
    if item.get("publication"):
        lines.append(f"来源: {item['publication']}")
    if item.get("url"):
        lines.append(f"URL: {item['url']}")
    paths = item.get("pdf_paths") or []
    if paths:
        lines.append("本地 PDF:")
        lines.extend(f"  - {p}" for p in paths)
    else:
        lines.append("本地 PDF: 无（检查是否已下载附件）")
    return "\n".join(lines)


@mcp.tool()
def ingest_from_zotero(key: str, engine: str = "local") -> str:
    """从 Zotero 条目导入 PDF 到精读库（解析本地附件路径后调用 ingest_pdf）。

    幂等：同一 PDF 重复导入会返回已存在的 paper_id。导入成功后即可用
    search_papers / read_paper_section 深读。

    Args:
        key: Zotero item key 或 itemID（用 search_zotero 获取）。
        engine: "local"（默认，免费）或 "datalab"（高保真按页计费）。
    """
    from . import zotero
    from .embedder import EmbeddingClient

    try:
        item = zotero.get_item(key)
    except Exception as exc:  # noqa: BLE001
        return f"读取 Zotero 条目失败: {type(exc).__name__}: {exc}"
    if "error" in item:
        return str(item["error"])

    paths = item.get("pdf_paths") or []
    if not paths:
        return (
            f"条目 [{item['key']}] {item['title'][:60]}\n"
            "未找到本地 PDF 附件。请在 Zotero 中下载附件，或用 ingest_pdf 传入绝对路径。"
        )

    pdf_path = paths[0]
    try:
        report = _ingest_pdf(pdf_path, engine=engine, embedder=EmbeddingClient.from_env())
    except Exception as exc:  # noqa: BLE001
        return f"导入失败（{pdf_path}）: {type(exc).__name__}: {exc}"

    if report["status"] == "duplicate":
        return (
            f"已存在于精读库（paper_id={report['paper_id']}）: {report['title']}\n"
            f"Zotero key={item['key']}"
        )
    if report["status"] != "ok":
        return str(report)
    cost = report.get("cost_usd")
    cost_line = f"费用: ${cost:.4f}｜" if cost is not None else ""
    return (
        f"从 Zotero 导入成功 paper_id={report['paper_id']}: {report['title']}\n"
        f"Zotero key={item['key']}｜年份: {report.get('year')}｜"
        f"DOI: {report.get('doi') or '未识别'}\n"
        f"{cost_line}切块: {report['chunks']}｜引擎: {report['engine']}\n"
        "后续可用 search_papers / read_paper_section 深读。"
    )


@mcp.tool()
def search_graph(query: str, top_k: int = 5) -> str:
    """概念图检索：按研究概念/方法/任务查找论文（与 search_papers 互补）。

    从概念图中找到与问题相关的实体（如 LightRAG、知识图谱、双层级检索），
    沿关系扩展一跳邻居，再回溯到讨论这些概念的论文与章节。适合
    “哪些论文用了 X 方法”“X 和 Y 有什么关系”这类概念性问题——
    普通语义搜索对这类问题命中率低。

    Args:
        query: 概念性问题或术语（如 “graph rag 双层检索”）。
        top_k: 返回论文数，默认 5。
    """
    from . import kg

    conn = db.connect()
    try:
        res = kg.search_graph(
            conn, query, top_k=top_k, embedder=EmbeddingClient.from_env()
        )
        return kg.format_graph_result(res, query)
    finally:
        conn.close()


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
