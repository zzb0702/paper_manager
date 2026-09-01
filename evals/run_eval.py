# -*- coding: utf-8 -*-
"""Run retrieval evaluation cases against the paper library.

Measures recall@k (any-of / all-of expectations), MRR and query latency
over evals/cases.json against the deterministic eval library
(evals/library/, built by build_library.py — never your real data/).

    python evals/build_library.py        # once, or after changing papers
    python evals/run_eval.py             # recall@5 + MRR + latency
    python evals/run_eval.py --k 3 --min-recall 0.85
    python evals/run_eval.py --json      # machine-readable summary

Exit code is non-zero when recall@k falls below --min-recall, so this can
gate refactors. Query rewriting is OFF by default (deterministic, no LLM);
enable with --rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
# must be set BEFORE importing paper_manager (config resolves it at import)
os.environ.setdefault("PAPER_MANAGER_DATA_DIR", str(ROOT / "evals" / "library"))
sys.path.insert(0, str(ROOT))

from paper_manager import db, retriever  # noqa: E402
from paper_manager.embedder import EmbeddingClient, RerankerClient  # noqa: E402

CASES_PATH = ROOT / "evals" / "cases.json"


def resolve_ref(conn: sqlite3.Connection, ref: str) -> int:
    rows = conn.execute("SELECT id, title FROM papers").fetchall()
    matches = [r["id"] for r in rows if ref.lower() in r["title"].lower()]
    if not matches:
        raise ValueError(f"用例引用的论文不在库中: {ref!r} — 先跑 build_library.py 或修正 cases.json")
    if len(matches) > 1:
        raise ValueError(f"用例引用 {ref!r} 命中多篇论文，请用更长的标题片段")
    return matches[0]


def evaluate_case(
    conn, case: dict[str, Any], k: int, emb, rr, rewriter
) -> dict[str, Any]:
    expect_any = case.get("expect_any", [])
    expect_all = case.get("expect_all", [])
    expected = {
        ref: resolve_ref(conn, ref) for ref in (expect_any + expect_all)
    }
    filters = case.get("filters", {})

    t0 = time.perf_counter()
    hits = retriever.search(
        conn,
        case["query"],
        top_k=k,
        embedder=emb,
        reranker=rr,
        year_min=filters.get("year_min"),
        year_max=filters.get("year_max"),
        author=filters.get("author"),
        venue=filters.get("venue"),
        query_rewriter=rewriter,
    )
    ms = (time.perf_counter() - t0) * 1000

    got = [h["paper_id"] for h in hits]
    ranks = {pid: i + 1 for i, pid in enumerate(got)}
    found = {ref: ranks.get(pid) for ref, pid in expected.items()}

    if expect_all:
        passed = all(found[ref] for ref in expect_all)
    else:
        passed = any(found[ref] for ref in expect_any)
    best_rank = min((r for r in found.values() if r), default=None)

    return {
        "id": case["id"],
        "query": case["query"],
        "note": case.get("note", ""),
        "passed": passed,
        "best_rank": best_rank,
        "mrr": (1.0 / best_rank) if best_rank else 0.0,
        "ms": round(ms),
        "top_titles": [h["title"][:48] for h in hits[:3]],
        "missed": [ref for ref in (expect_any + expect_all) if not found[ref]],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="论文检索评测")
    ap.add_argument("--k", type=int, default=5, help="每条用例取 top-k（默认 5）")
    ap.add_argument("--min-recall", type=float, default=0.9,
                    help="recall@k 低于该值时退出码为 1（默认 0.9）")
    ap.add_argument("--rewrite", action="store_true",
                    help="启用 LLM 查询改写（默认关闭以保证确定性）")
    ap.add_argument("--json", dest="as_json", action="store_true",
                    help="输出机器可读 JSON")
    args = ap.parse_args()

    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    conn = db.connect()
    n_papers = conn.execute("SELECT COUNT(*) c FROM papers").fetchone()["c"]
    if n_papers == 0:
        print("评测库为空：先运行 python evals/build_library.py")
        sys.exit(1)

    emb = EmbeddingClient.from_env()
    rr = RerankerClient.from_env()
    rewriter = None
    if args.rewrite:
        if not os.getenv("LLM_BASE_URL", "").strip():
            print("--rewrite 需要 LLM_BASE_URL，已忽略")
        else:
            from paper_manager.llm import rewrite_query
            rewriter = rewrite_query

    results = [evaluate_case(conn, c, args.k, emb, rr, rewriter) for c in cases]

    passed = sum(1 for r in results if r["passed"])
    recall = passed / len(results)
    mrr = statistics.mean(r["mrr"] for r in results)
    latencies = sorted(r["ms"] for r in results)
    median_ms = statistics.median(latencies)
    p95_ms = latencies[max(0, int(len(latencies) * 0.95) - 1)]

    if args.as_json:
        print(json.dumps(
            {
                "k": args.k,
                "cases": len(results),
                "recall": round(recall, 4),
                "mrr": round(mrr, 4),
                "median_ms": median_ms,
                "p95_ms": p95_ms,
                "passed": passed,
                "failures": [r for r in results if not r["passed"]],
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        print(f"{'id':6s} {'ok':>3s} {'rank':>4s} {'ms':>6s}  query")
        for r in results:
            mark = "✓" if r["passed"] else "✗"
            rank = str(r["best_rank"] or "-")
            print(f"{r['id']:6s} {mark:>3s} {rank:>4s} {r['ms']:>6d}  {r['query'][:52]}")
        print(f"\n用例 {len(results)}｜通过 {passed}｜recall@{args.k} {recall:.2%}｜MRR {mrr:.3f}")
        print(f"延迟  中位 {median_ms:.0f}ms｜p95 {p95_ms:.0f}ms")
        failures = [r for r in results if not r["passed"]]
        if failures:
            print("\n未通过用例：")
            for r in failures:
                print(f"  {r['id']}: {r['query']}")
                print(f"    期望: {r['missed']}")
                for t in r["top_titles"]:
                    print(f"    实际 top: {t}")
    conn.close()

    if recall < args.min_recall:
        print(f"\nFAIL: recall@{args.k} {recall:.2%} < 阈值 {args.min_recall:.0%}")
        sys.exit(1)


if __name__ == "__main__":
    main()
