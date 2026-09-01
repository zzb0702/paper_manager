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
