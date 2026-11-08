"""Cross-encoder reranking via a llama.cpp server (--reranking) on /v1/rerank."""

from __future__ import annotations

import json
import os
import sys
import urllib.request

URL = os.environ.get("RERANK_URL", "")
# Per-passage cap. bge-reranker-v2-m3 takes 8k tokens; 512-token models (MiniLM) need ~1000 or the request 400s.
MAX_CHARS = int(os.environ.get("RERANK_MAX_CHARS", "2000"))


def rerank(query: str, passages: list[str], timeout: float = 10) -> list[float] | None:
    """Relevance score per passage (same order), or None if the reranker is off or unreachable."""
    if not URL or not passages:
        return None
    body = {"query": query, "documents": [p[:MAX_CHARS] for p in passages], "top_n": len(passages)}
    req = urllib.request.Request(URL + "/v1/rerank", json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            results = json.loads(r.read())["results"]
    except Exception as e:  # noqa: BLE001 - any failure falls back to RRF order
        print(f"rerank unavailable, using RRF order: {e}", file=sys.stderr, flush=True)
        return None
    scores = [0.0] * len(passages)
    for x in results:
        scores[x["index"]] = float(x["relevance_score"])
    return scores
