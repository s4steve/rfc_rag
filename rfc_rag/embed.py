"""Ollama embeddings (nomic-embed-text, 768-d)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("EMBED_MODEL", "nomic-embed-text")
BATCH = 32
MAX_CHARS = 6000  # nomic's context in Ollama defaults to 2048 tokens; keep inputs well under it


def _post(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(URL + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ensure_model() -> None:
    try:
        _post("/api/show", {"model": MODEL})
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        print(f"pulling {MODEL} ...", flush=True)
        _post("/api/pull", {"model": MODEL, "stream": False}, timeout=3600)


def embed(texts: list[str], query: bool = False) -> list[list[float]]:
    prefix = "search_query: " if query else "search_document: "
    out = []
    for i in range(0, len(texts), BATCH):
        batch = [prefix + t[:MAX_CHARS] for t in texts[i : i + BATCH]]
        out += _post("/api/embed", {"model": MODEL, "input": batch})["embeddings"]
    return out


def vec(v: list[float]) -> str:
    """pgvector text literal, so no extra adapter dependency."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
