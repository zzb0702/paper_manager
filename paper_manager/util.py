# -*- coding: utf-8 -*-
"""Shared helpers.

log(): everything the library prints must go to stderr — inside the MCP
stdio server, stdout carries the JSON-RPC protocol and a stray print
would corrupt it.
"""

from __future__ import annotations

import sys


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def format_authors(authors: str | None, max_names: int = 3) -> str:
    """Display helper: keep at most max_names authors, then 「等」."""
    if not authors or not authors.strip():
        return ""
    parts = [p.strip() for p in authors.split(";") if p.strip()]
    if not parts:
        return ""
    if len(parts) <= max_names:
        return "; ".join(parts)
    return "; ".join(parts[:max_names]) + " 等"
