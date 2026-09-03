# -*- coding: utf-8 -*-
"""Paths and environment loading.

The .env key names are intentionally kept generic (SILICONFLOW / LLM /
DATALAB) so one credentials file can be reused across local projects.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Load keys once at import; real env vars always win over the file.
load_dotenv(ROOT / ".env", override=False)

DATA_DIR = Path(
    os.getenv("PAPER_MANAGER_DATA_DIR", str(ROOT / "data"))
).resolve()
MD_DIR = DATA_DIR / "markdown"
DB_PATH = DATA_DIR / "papers.db"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MD_DIR.mkdir(parents=True, exist_ok=True)
