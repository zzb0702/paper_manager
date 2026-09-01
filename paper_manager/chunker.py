# -*- coding: utf-8 -*-
"""Markdown-aware chunking with section paths and page ranges.

- Sections tracked by ATX headings (# / ## / ###).
- Page numbers tracked from "<!-- page:N -->" markers (local engine).
- Paragraph blocks merged up to max_chars; oversized blocks split with
  character overlap so sentences are not lost at the seam.
"""

from __future__ import annotations

import re
from typing import Any

PAGE_MARK = re.compile(r"<!--\s*page:(\d+)\s*-->")
HEADING = re.compile(r"^(#{1,4})\s+(.*)$")

DEFAULT_MAX_CHARS = 1800
DEFAULT_OVERLAP = 200


def _blocks(markdown: str) -> list[dict[str, Any]]:
    sections: list[str] = []
    page = None
    buf: list[str] = []

    def flush() -> dict[str, Any] | None:
        text = "\n".join(buf).strip()
        buf.clear()
        if not text:
            return None
        return {
            "section": " / ".join(sections) or "正文",
            "page_start": page,
            "page_end": page,
            "text": re.sub(r"\s+\n", "\n", text),
        }

    out: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        m = PAGE_MARK.search(line)
        if m:
            if buf:
                blk = flush()
                if blk:
                    out.append(blk)
            page = int(m.group(1))
            continue
        h = HEADING.match(line)
        if h:
            blk = flush()
            if blk:
                out.append(blk)
            depth = len(h.group(1))
            title = h.group(2).strip()
            sections = sections[: depth - 1]
            sections.append(title)
            continue
        if not line.strip() and buf:
            blk = flush()
            if blk:
                out.append(blk)
            continue
        buf.append(line)
    if buf:
        blk = flush()
        if blk:
            out.append(blk)
    return out


def chunk_markdown(
    markdown: str,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def push_current() -> None:
        nonlocal current
        if current and current["text"].strip():
            chunks.append(current)
        current = None

    for blk in _blocks(markdown):
        same = (
            current is not None
            and current["section"] == blk["section"]
            and len(current["text"]) + len(blk["text"]) + 2 <= max_chars
        )
        if same:
            assert current is not None
            current["text"] += "\n\n" + blk["text"]
            if blk["page_end"] is not None:
                current["page_end"] = blk["page_end"]
            continue

        # oversized block: split by chars with overlap
        if len(blk["text"]) > max_chars:
            push_current()
            text = blk["text"]
            start = 0
            while start < len(text):
                piece = text[start : start + max_chars].strip()
                if piece:
                    chunks.append(
                        {
                            "section": blk["section"],
                            "page_start": blk["page_start"],
                            "page_end": blk["page_end"],
                            "text": piece,
                        }
                    )
                start += max_chars - overlap
            continue

        push_current()
        current = dict(blk)
    push_current()
    return chunks
