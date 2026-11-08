# RFC RAG

A searchable store of DNS RFCs and active DNS Internet-Drafts, served over MCP. Every result carries the document's status (Internet/Proposed/Draft Standard, BCP, Informational, Historic, obsoleted, or Internet-Draft), so answers stay grounded in what is current.

## Run

```bash
docker compose up -d                                   # postgres+pgvector, ollama, MCP server on 127.0.0.1:8000
docker compose run --rm ingest                         # first run: downloads ~450 docs and embeds them (CPU Ollama: ~20-30 min)
claude mcp add --transport http rfc-rag http://localhost:8000/mcp
```

Re-running `ingest` is incremental: unchanged chunks are not re-embedded. To pick up newly published RFCs, errata and drafts, run `docker compose run --rm ingest python -m rfc_rag.ingest --refresh`. To re-process a few documents, add `--only RFC1035,RFC9460`. For Metal-accelerated embeddings, set `OLLAMA_URL=http://host.docker.internal:11434` and use the Ollama app on the host.

Checks:

```bash
docker compose run --rm ingest python -m pytest -q tests   # parser unit tests
docker compose run --rm ingest python -m tests.eval        # golden questions -> recall@5
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
6. **Search**: hybrid. pgvector cosine and Postgres full-text results are fused with reciprocal rank, and status filters go in the SQL.

## MCP tools

| Tool | What it does |
|---|---|
| `search(query, k, include_obsolete, include_drafts, statuses, docs)` | Hybrid search. Defaults to current RFCs only |
| `get_document(doc)` | Metadata, relations, TOC, errata count |
| `get_section(doc, section)` | Full section text, its subsections, and its errata |
| `lineage(doc)` | Follows obsoleted-by links to the current spec(s), with updated-by lists and pending drafts |
| `list_documents(status, wg, doc_type, current_only, title_contains)` | Browse the corpus |
| `lookup_registry(query, registry)` | IANA code points (`65`, `HTTPS`, `ECDSAP256SHA256`, …) with the defining RFCs' status |

The server has no auth, so it is only bound to localhost. Put an authenticating proxy in front before exposing it elsewhere.
