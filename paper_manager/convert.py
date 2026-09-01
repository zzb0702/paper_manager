# -*- coding: utf-8 -*-
"""PDF -> Markdown via two engines.

local   : PyMuPDF text extraction, free/offline, inserts page markers.
datalab : Marker cloud API (paid per page), high fidelity for scanned
          pages / formulas / tables. Same API the local datalab-pdf MCP
          server uses.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import requests

# Some OpenAI-compatible gateways 403 the SDK default UA; browser UA is safe.
UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

DATALAB_API_URL = "https://www.datalab.to/api/v1/convert"


def convert_local(pdf_path: str | Path) -> dict[str, Any]:
    import pymupdf

    doc = pymupdf.open(str(pdf_path))
    parts: list[str] = []
    full_text = ""
    for i, page in enumerate(doc, 1):
        text = page.get_text("text").strip()
        full_text += text + "\n"
        parts.append(f"\n\n<!-- page:{i} -->\n\n{text}")
    md = "\n".join(parts).strip()
    meta = dict(doc.metadata or {})
    return {
        "markdown": md,
        "page_count": doc.page_count,
        "meta": meta,
        "front_text": full_text[:4000],
    }


def convert_datalab(
    pdf_path: str | Path, mode: str = "balanced"
) -> dict[str, Any]:
    api_key = os.getenv("DATALAB_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("未配置 DATALAB_API_KEY，无法使用 datalab 引擎")
    if mode not in ("fast", "balanced", "accurate"):
        raise ValueError(f"mode 必须是 fast|balanced|accurate: {mode}")

    with open(pdf_path, "rb") as f:
        resp = requests.post(
            DATALAB_API_URL,
            files={"file": (Path(pdf_path).name, f, "application/pdf")},
            data={"output_format": "markdown", "mode": mode},
            headers={"X-API-Key": api_key},
            timeout=300,
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"Datalab 提交失败 HTTP {resp.status_code}: {resp.text[:300]}"
        )
    check_url = resp.json()["request_check_url"]

    result: dict[str, Any] = {}
    for _ in range(240):  # ~8 min
        result = requests.get(
            check_url, headers={"X-API-Key": api_key}, timeout=60
        ).json()
        if result.get("status") == "complete":
            break
        time.sleep(2)
    else:
        raise RuntimeError("Datalab 转换超时（约 8 分钟）")
    if not result.get("success"):
        raise RuntimeError(
            f"Datalab 转换失败: {str(result.get('error'))[:300]}"
        )
    markdown = result.get("markdown") or ""

    # local metadata pass for title/doi/year (cheap, no second conversion)
    meta: dict[str, Any] = {}
    front_text = ""
    try:
        import pymupdf

        doc = pymupdf.open(str(pdf_path))
        meta = dict(doc.metadata or {})
        front_text = "".join(p.get_text("text") for p in doc[:4])
    except Exception:
        pass
    return {
        "markdown": markdown,
        "page_count": result.get("page_count"),
        "meta": meta,
        "front_text": (front_text or markdown)[:4000],
    }


_TITLE_JUNK = re.compile(r"^(arxiv|doi|http|www\.|proceedings|preprint)", re.I)
_PAGE_MARK = re.compile(r"<!--\s*page:\d+\s*-->")


def guess_title(markdown: str, meta: dict | None) -> str:
    meta_title = (meta or {}).get("title") or ""
    if meta_title.strip() and len(meta_title.strip()) > 6:
        return meta_title.strip()[:200]
    for line in markdown.splitlines():
        s = line.strip()
        if s.startswith("<!--") or _PAGE_MARK.search(s):
            continue  # page markers / html comments are not titles
        s = s.lstrip("#").strip()
        if len(s) >= 8 and not _TITLE_JUNK.match(s):
            return s[:200]
    return "untitled"
