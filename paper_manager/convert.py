# -*- coding: utf-8 -*-
"""PDF -> Markdown via two engines.

local   : PyMuPDF text extraction, free/offline, inserts page markers.
datalab : Marker cloud API (paid per page), high fidelity for scanned
          pages / formulas / tables. Supports multiple API keys with
          automatic rotation: when a key's balance is exhausted (HTTP 402
          or payment-related error) the next key is used automatically.
          Exhausted keys are remembered in a state file for EXHAUST_TTL
          hours, then retried (top-ups happen outside this tool).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from .util import log

# Some OpenAI-compatible gateways 403 the SDK default UA; browser UA is safe.
UA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}

DATALAB_API_URL = "https://www.datalab.to/api/v1/convert"
EXHAUST_TTL_SECONDS = 6 * 3600  # retry an exhausted key after 6h


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


# ---------------------------------------------------------------- datalab

def _load_datalab_keys() -> list[str]:
    """DATALAB_API_KEYS (comma separated) wins; falls back to DATALAB_API_KEY."""
    raw = os.getenv("DATALAB_API_KEYS", "").strip()
    if not raw:
        raw = os.getenv("DATALAB_API_KEY", "").strip()
    seen: set[str] = set()
    keys: list[str] = []
    for k in (s.strip() for s in raw.split(",")):
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def _is_quota_error(status_code: int, body: str) -> bool:
    if status_code == 402:  # Payment Required
        return True
    low = (body or "").lower()
    return any(
        w in low
        for w in ("payment", "credit", "quota", "insufficient", "余额", "额度")
    )


class DatalabKeyPool:
    """Rotating DATALAB keys; exhausted keys are blacklisted with a TTL.

    The state file stores sha256 prefixes only — raw keys never touch disk
    beyond the .env they came from.
    """

    def __init__(self, keys: list[str], state_path: Path):
        self.keys = keys
        self.state_path = state_path
        self.exhausted: dict[str, float] = {}
        self._load()

    @classmethod
    def from_env(cls) -> "DatalabKeyPool":
        from .config import DATA_DIR

        override = os.getenv("DATALAB_KEY_STATE", "").strip()
        state = Path(override) if override else DATA_DIR / "datalab_keys.json"
        return cls(_load_datalab_keys(), state)

    def _hash(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def _load(self) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            now = time.time()
            self.exhausted = {
                h: ts for h, ts in data.items() if now - float(ts) < EXHAUST_TTL_SECONDS
            }
        except Exception:
            self.exhausted = {}

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps(self.exhausted), encoding="utf-8"
            )
        except Exception:
            pass

    def candidates(self) -> list[str]:
        return [k for k in self.keys if self._hash(k) not in self.exhausted]

    def mark_exhausted(self, key: str) -> None:
        self.exhausted[self._hash(key)] = time.time()
        self._save()

    def status(self) -> str:
        return f"{len(self.candidates())}/{len(self.keys)} 个 DATALAB key 可用"


def _datalab_poll(check_url: str, api_key: str) -> dict[str, Any]:
    for _ in range(240):  # ~8 min
        result = requests.get(
            check_url, headers={"X-API-Key": api_key}, timeout=60
        ).json()
        if result.get("status") == "complete":
            return result
        time.sleep(2)
    raise RuntimeError("Datalab 转换超时（约 8 分钟）")


def convert_datalab(
    pdf_path: str | Path, mode: str = "balanced"
) -> dict[str, Any]:
    if mode not in ("fast", "balanced", "accurate"):
        raise ValueError(f"mode 必须是 fast|balanced|accurate: {mode}")
    pool = DatalabKeyPool.from_env()
    if not pool.keys:
        raise RuntimeError(
            "未配置 DATALAB_API_KEYS / DATALAB_API_KEY，无法使用 datalab 引擎"
        )

    candidates = pool.candidates()
    last_error: Exception | None = None
    for idx, key in enumerate(candidates, 1):
        try:
            with open(pdf_path, "rb") as f:
                resp = requests.post(
                    DATALAB_API_URL,
                    files={"file": (Path(pdf_path).name, f, "application/pdf")},
                    data={"output_format": "markdown", "mode": mode},
                    headers={"X-API-Key": key},
                    timeout=300,
                )
        except requests.RequestException as exc:
            last_error = exc
            log(f"  [DATALAB key#{idx}] 网络错误，尝试下一个: {exc}")
            continue

        if resp.status_code == 200:
            result = _datalab_poll(resp.json()["request_check_url"], key)
            if not result.get("success"):
                raise RuntimeError(
                    f"Datalab 转换失败: {str(result.get('error'))[:300]}"
                )
            markdown = result.get("markdown") or ""
            breakdown = result.get("cost_breakdown") or {}
            cents = breakdown.get("final_cost_cents", result.get("total_cost"))
            return {
                "markdown": markdown,
                "page_count": result.get("page_count"),
                "cost_usd": (cents / 100) if cents is not None else None,
                "key_index": idx,
                "keys_available": pool.status(),
                "meta": {},
                "front_text": markdown[:4000],
            }

        body = resp.text[:300]
        if _is_quota_error(resp.status_code, body):
            pool.mark_exhausted(key)
            log(
                f"  [DATALAB key#{idx}] 额度不足，自动切换下一个"
                f"（{pool.status()}）: {body[:120]}"
            )
            last_error = RuntimeError(f"key#{idx} HTTP {resp.status_code}: {body}")
            continue
        raise RuntimeError(f"Datalab 提交失败 HTTP {resp.status_code}: {body}")

    raise RuntimeError(
        f"所有 DATALAB key 均不可用（{pool.status()}，耗尽记录 {EXHAUST_TTL_SECONDS // 3600}h 后自动重试）。"
        f"最后错误: {last_error}"
    )


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
