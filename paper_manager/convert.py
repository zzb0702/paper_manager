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
            # 提交成功即已按 key 计费；轮询阶段的网络异常换 key 重提交会
            # 重复扣费，所以这里不切换 key，只把错误说清楚。
            try:
                check_url = resp.json()["request_check_url"]
                result = _datalab_poll(check_url, key)
            except requests.RequestException as exc:
                raise RuntimeError(
                    f"[DATALAB key#{idx}] 轮询结果时网络异常"
                    f"（转换可能已计费，请稍后用 --force 重试）: {exc}"
                ) from exc
            if not result.get("success"):
                raise RuntimeError(
                    f"Datalab 转换失败: {str(result.get('error'))[:300]}"
                )
            markdown = result.get("markdown") or ""
            breakdown = result.get("cost_breakdown") or {}
            cents = breakdown.get("final_cost_cents", result.get("total_cost"))

            # 云端转换不回传元数据：用 PyMuPDF 本地补一次 author/date
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
                "cost_usd": (cents / 100) if cents is not None else None,
                "key_index": idx,
                "keys_available": pool.status(),
                "meta": meta,
                "front_text": (front_text or markdown)[:4000],
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
_ABSTRACT_HEAD = re.compile(
    r"^(?:\d+\.?\s*)?(?:abstract|摘要|summary)\s*[:.]?\s*$"
    r"|^(?:\d+\.?\s+)?introduction\b",
    re.I,
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
_URL_RE = re.compile(r"https?://\S+", re.I)
_AFFILIATION_RE = re.compile(
    r"\b("
    r"university|institute|department|laboratory|lab\b|college|school|"
    r"research center|research centre|academy|center for|centre for|"
    r"corporation|inc\.?\b|ltd\.?\b|"
    r"microsoft|google|deepmind|openai|facebook|amazon\b|"
    r"arxiv|eth zurich|tsinghua|peking university"
    r")\b",
    re.I,
)
# Affiliation superscripts glued to names: "Guo1,2" / "Edge1†" / "Wang†§" (commas stay)
_AFFIX_MARK = re.compile(r"[\d†‡§*∗]+")
# Function words that almost never appear in a bare author list
_TITLE_STOP = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "from",
    "with", "via", "using", "towards", "toward", "over", "under",
    "how", "what", "why", "when",
}


def _clean_line(s: str) -> str:
    s = s.replace(" ", " ").replace("﻿", "")
    s = _PAGE_MARK.sub("", s)
    s = re.sub(r"^\s*page\s*\d+\s*$", "", s, flags=re.I)
    return s.strip()


def _strip_front_noise(lines: list[str]) -> list[str]:
    out = list(lines)
    while out:
        s = out[0]
        if (
            not s
            or s.isdigit()
            or _TITLE_JUNK.match(s)
            or s.lower().startswith("arxiv:")
            or re.fullmatch(r"[\d\s\-.]+", s or "")
        ):
            out.pop(0)
            continue
        break
    return out


def _looks_like_person_token(token: str) -> bool:
    t = token.strip().strip(".,;:")
    if not t or len(t) < 2:
        return False
    if _AFFILIATION_RE.search(t) or _EMAIL_RE.search(t):
        return False
    if not re.match(r"^[A-ZÀ-Þ]", t):
        return False
    core = re.sub(r"[^A-Za-zÀ-ÿ]", "", t)
    if not core:
        return False
    # ALL-CAPS acronyms (IBM, RAG, GPT) are not person tokens
    if core.isupper() and len(core) >= 2:
        return False
    return True


def _is_name_chunk(chunk: str) -> bool:
    words = chunk.replace("-", " ").split()
    if not (2 <= len(words) <= 4):
        return False
    if not all(_looks_like_person_token(w) for w in words):
        return False
    lower = {w.lower() for w in words}
    return not (lower & _TITLE_STOP)


def _has_affix_marker(s: str) -> bool:
    return bool(re.search(r"[\d†‡*∗]", s))


def _is_author_line(s: str) -> bool:
    """Author list line: 2+ comma-separated names, or one name with affix markers."""
    if not s or len(s) > 160:
        return False
    if _EMAIL_RE.search(s) or _URL_RE.search(s) or _AFFILIATION_RE.search(s):
        return False
    if _ABSTRACT_HEAD.match(s):
        return False
    if s.startswith("{") or s.startswith("†") or s.startswith("*"):
        return False

    cleaned = _AFFIX_MARK.sub(" ", s)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;")
    if not cleaned:
        return False

    chunks = [c.strip() for c in re.split(r"\s*[,;]\s*", cleaned) if c.strip()]
    name_chunks = [c for c in chunks if _is_name_chunk(c)]

    # Multi-author line: "Zirui Guo, Lianghao Xia, …"
    if len(name_chunks) >= 2 and len(name_chunks) >= max(2, len(chunks) - 1):
        return True

    # Single person with superscript markers: "Darren Edge1†"
    if len(chunks) == 1 and len(name_chunks) == 1 and _has_affix_marker(s):
        return True

    return False


def _parse_name_chunk(chunk: str) -> str:
    return re.sub(r"\s+", " ", chunk).strip(" ,;.")


def _parse_author_line(s: str) -> list[str]:
    cleaned = _AFFIX_MARK.sub(" ", s)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;")
    if not cleaned:
        return []

    # "Last, First; Last, First"
    if ";" in cleaned:
        out: list[str] = []
        for part in cleaned.split(";"):
            part = part.strip()
            if not part:
                continue
            if "," in part:
                last, first = part.split(",", 1)
                name = f"{first.strip()} {last.strip()}".strip()
            else:
                name = part
            if _is_name_chunk(name) or (
                len(name.split()) >= 2 and _looks_like_person_token(name.split()[0])
            ):
                out.append(_parse_name_chunk(name))
        return out

    # "First Last, First Last, …"
    if "," in cleaned:
        names: list[str] = []
        for part in cleaned.split(","):
            name = _parse_name_chunk(part)
            if name and (
                _is_name_chunk(name)
                or (
                    len(name.split()) >= 2
                    and _looks_like_person_token(name.split()[0])
                )
            ):
                names.append(name)
        if names:
            return names

    name = _parse_name_chunk(cleaned)
    if _is_name_chunk(name):
        return [name]
    return []


def extract_authors(front_text: str, meta: dict | None = None) -> str:
    """Authors from PDF Info, else reconstructed from first-page text."""
    meta_auth = ((meta or {}).get("author") or "").strip()
    if meta_auth:
        return meta_auth[:300]

    lines = _strip_front_noise([_clean_line(x) for x in (front_text or "").splitlines()])
    start = None
    for i, line in enumerate(lines[:40]):
        if _is_author_line(line):
            start = i
            break
    if start is None:
        return ""

    names: list[str] = []
    for line in lines[start : start + 40]:
        if not line:
            if names:
                break
            continue
        if (
            _EMAIL_RE.search(line)
            or _AFFILIATION_RE.search(line)
            or _ABSTRACT_HEAD.match(line)
        ):
            break
        if line.startswith("{") or "†These" in line or line.startswith("†"):
            break
        batch = _parse_author_line(line)
        if not batch:
            if names:
                break
            continue
        for n in batch:
            if n not in names:
                names.append(n)
        if len(names) >= 30:
            break
    return "; ".join(names)[:300]


def extract_title(front_text: str, meta: dict | None = None) -> str:
    """Prefer a multi-line title from the first page over truncated PDF Info."""
    lines = _strip_front_noise([_clean_line(x) for x in (front_text or "").splitlines()])

    title_lines: list[str] = []
    for line in lines[:25]:
        if not line:
            if title_lines:
                break
            continue
        if _ABSTRACT_HEAD.match(line):
            break
        if _is_author_line(line) or _EMAIL_RE.search(line) or _AFFILIATION_RE.search(line):
            break
        # Affix markers on a short line => authors, not title
        if _has_affix_marker(line) and len(line) < 80 and title_lines:
            break
        if len(title_lines) == 0 and len(line) > 180:
            break
        if len(title_lines) == 0 and (
            line.lower().startswith("keywords")
            or line.lower() == "keywords"
            or _AFFILIATION_RE.match(line)
        ):
            break
        if len(line) < 3:
            continue
        title_lines.append(line)
        if len(title_lines) >= 4:
            break

    front_title = re.sub(r"\s+", " ", " ".join(title_lines)).strip(" -–—·")
    meta_title = ((meta or {}).get("title") or "").strip()

    if not front_title:
        return meta_title[:200] or "untitled"
    if not meta_title or len(meta_title) < 8:
        return front_title[:200]

    mt, ft = meta_title.lower(), front_title.lower()
    # PDF Info titles on arXiv are often truncated mid-phrase
    if ft.startswith(mt[: min(24, len(mt))]) or mt.startswith(ft[: min(24, len(ft))]):
        return (front_title if len(front_title) >= len(meta_title) else meta_title)[:200]
    if len(front_title) > len(meta_title) + 8:
        return front_title[:200]
    return meta_title[:200]


def guess_title(markdown: str, meta: dict | None) -> str:
    meta_title = (meta or {}).get("title") or ""
    if meta_title.strip() and len(meta_title.strip()) > 6:
        rebuilt = extract_title(markdown, meta)
        if rebuilt and rebuilt != "untitled" and len(rebuilt) > len(meta_title.strip()) + 4:
            return rebuilt[:200]
        return meta_title.strip()[:200]
    return extract_title(markdown, meta)
