# RFC RAG

A searchable store of DNS RFCs and active DNS Internet-Drafts, served over MCP. Every result carries the document's status (Internet/Proposed/Draft Standard, BCP, Informational, Historic, obsoleted, or Internet-Draft), so answers stay grounded in what is current.

## Run

```bash
docker compose up -d                                   # postgres+pgvector, ollama, reranker, MCP server on 127.0.0.1:8000
docker compose run --rm ingest                         # first run: downloads ~450 docs and embeds them (CPU Ollama: ~20-30 min)
claude mcp add --transport http rfc-rag http://localhost:8000/mcp
```

Re-running `ingest` is incremental: unchanged chunks are not re-embedded. To pick up newly published RFCs, errata and drafts, run `docker compose run --rm ingest python -m rfc_rag.ingest --refresh`. To re-process a few documents, add `--only RFC1035,RFC9460`. For Metal-accelerated embeddings, set `OLLAMA_URL=http://host.docker.internal:11434` and use the Ollama app on the host.

Checks:

```bash
docker compose run --rm ingest python -m pytest -q tests   # parser unit tests
docker compose run --rm ingest python -m tests.eval        # 96 golden questions, rerank off vs on (~8 min)
```

## Pipeline

1. **Fetch** (`fetch.py`): pulls the RFC Editor index (metadata for all ~9.9k RFCs, so the graph tools work), RFC text, errata (Verified and Held for Document Update), IANA DNS registries, and active drafts from Datatracker. Everything is cached under `data/raw/`.
2. **Scope**: the DNS working groups, a seed list, a title regex, and one hop over obsoletes/updates edges.
3. **Parse** (`parse.py`, deterministic):
   - RFC 8650+ use the prepped v3 XML.
   - Older RFCs and all drafts use the text, with page headers and footers stripped and paragraphs rejoined across page breaks.
   - Headings are accepted only if their numbers run in sequence, so list items aren't mistaken for headings.
   - ASCII-art figures stay whole.
   - Boilerplate sections are dropped. References are stored but not embedded.
   - Output goes to `data/normalized/<doc>.json`.
4. **Chunk** (`chunk.py`): the parent is a whole section (in `sections`). Children are paragraph-packed chunks of about 1,000 chars, and figures and tables are never split. Each chunk is embedded with a context header, for example `RFC 2535 · Proposed Standard · OBSOLETED by 4033, 4034, 4035 · "…" · §4.1 …`. Each erratum is its own chunk, linked to its section.
5. **Embed**: Ollama `nomic-embed-text` (768-d).
6. **Search**: hybrid. pgvector cosine and Postgres full-text results are fused with reciprocal rank (RRF), and status filters go in the SQL. The top 20 sections are then re-scored by a cross-encoder (`bge-reranker-v2-m3` on a llama.cpp server).
7. **Graph**: `obsoletes`/`updates` come from the RFC index, STD/BCP membership from its `std-entry`/`bcp-entry` lists, and citation edges from each in-scope document's References sections (normative vs informative).

## MCP tools

| Tool | What it does |
|---|---|
| `search(query, k, include_obsolete, include_drafts, statuses, docs)` | Hybrid search plus rerank. Defaults to current RFCs only |
| `get_document(doc)` | Metadata, relations, STD/BCP membership, TOC, errata count, what it cites. `STD 13` returns both member RFCs |
| `cited_by(doc, current_only)` | DNS documents in the store whose References cite this one |
| `get_section(doc, section)` | Full section text, its subsections, and its errata |
| `lineage(doc)` | Follows obsoleted-by links to the current spec(s), with updated-by lists and pending drafts |
| `list_documents(status, wg, doc_type, current_only, title_contains)` | Browse the corpus |
| `lookup_registry(query, registry)` | IANA code points (`65`, `HTTPS`, `ECDSAP256SHA256`, …) with the defining RFCs' status |

Any tool taking a `doc` accepts `1035`, `RFC 1035`, `STD 13`, `BCP 91` or a draft name.

## Reranking

| Eval (96 questions) | doc recall@5 | section recall@5 | doc MRR@10 | p50 latency |
|---|---|---|---|---|
| RRF only | 89/96 | 25/30 | 0.718 | 63 ms |
| + rerank, pool 20 (default) | 91/96 | 27/30 | 0.779 | 4.8 s |
| + rerank, pool 12 | 89/96 | 26/30 | 0.780 | 3.3 s |
| + rerank, pool 30 | 89/96 | 28/30 | 0.768 | 6.1 s |

The latency comes from the reranker running on CPU inside Docker, about 0.2 s per passage. Settings:

- `RERANK_POOL` changes how many passages are reranked; cost grows linearly.
- `RERANK_URL=` (empty) turns reranking off.
- `RERANK_MODEL` swaps the GGUF.

Running `llama-server` natively on the Mac (Metal) and pointing `RERANK_URL=http://host.docker.internal:8080` at it should be several times faster. If the reranker is down, search falls back to RRF order and logs a warning.

Tried and rejected:

- `ms-marco-MiniLM-L12-v2`: fast, but MRR 0.625, worse than no reranking.
- `gte-multilingual-reranker-base`: not supported by this llama.cpp build.
- `Q4_K_M`: slower than `Q8_0` on CPU.

Resources: a 1.2 GB llama.cpp image, a 600 MB model in the `rerank` volume, and about 700 MB of RAM.

## Auth

With no `MCP_TOKEN`, the server has no auth and is only bound to localhost. To expose it, for example on Tailscale:

```bash
export MCP_TOKEN=$(openssl rand -hex 32) MCP_BIND=0.0.0.0 MCP_ALLOWED_HOSTS='myhost.example.ts.net:*'
docker compose up -d mcp
claude mcp add --transport http rfc-rag http://myhost.example.ts.net:8000/mcp --header "Authorization: Bearer $MCP_TOKEN"
```

Every request then needs the bearer token, and anything else gets a 401. `MCP_ALLOWED_HOSTS` extends the DNS-rebinding allow-list. Use `tailscale serve` in front if you want HTTPS.

## Schema changes

`schema.sql` is idempotent and runs at the start of every ingest, so new columns and tables reach an existing database without wiping it.
