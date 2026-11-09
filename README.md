# RFC RAG

A searchable store of DNS and email RFCs, active Internet-Drafts and individual proposals, served over MCP. Every result carries the document's status (Internet/Proposed/Draft Standard, BCP, Informational, Historic, obsoleted, or Internet-Draft), so answers stay grounded in what is current.

Email covers core mail, IMAP, POP, MIME, Sieve, JMAP, DKIM, SPF, DMARC, MTA-STS, S/MIME and OpenPGP. Fax, VPIM, TRADE, PEM and X.400 are out of scope. Individual drafts have `stream: individual`: they are proposals no working group has adopted.

## Run

```bash
docker compose up -d                                   # postgres+pgvector, ollama, reranker, MCP server on 127.0.0.1:8000
docker compose run --rm ingest                         # first run: downloads ~940 docs and embeds ~47k chunks (CPU Ollama: over an hour)
claude mcp add --transport http rfc-rag http://localhost:8000/mcp
```

Re-running `ingest` is incremental: unchanged chunks are not re-embedded. To pick up newly published RFCs, errata and drafts, run `docker compose run --rm ingest python -m rfc_rag.ingest --refresh`. To re-process a few documents, add `--only RFC1035,RFC9460`. For Metal-accelerated embeddings, set `OLLAMA_URL=http://host.docker.internal:11434` and use the Ollama app on the host.

Checks:

```bash
docker compose run --rm ingest python -m pytest -q tests   # parser unit tests
docker compose run --rm ingest python -m tests.eval        # 96 DNS questions, rerank off vs on (~8 min)
docker compose run --rm ingest python -m tests.eval --topic email   # 64 email questions (~6 min); add --scoped to filter search by topic
```

## Pipeline

1. **Fetch** (`fetch.py`): pulls the RFC Editor index (metadata for all ~9.9k RFCs, so the graph tools work), RFC text, errata (Verified and Held for Document Update), IANA DNS and mail registries, and active drafts from Datatracker. Everything is cached under `data/raw/`.
2. **Scope** (`TOPICS` in `fetch.py`): per topic, the working groups, a seed list and a title regex. DNS adds one hop over obsoletes/updates edges; email follows obsoletes only, because updates edges pull in SIP and X.509. A document can have both topics. Individual drafts are found by whole-token name match.
3. **Parse** (`parse.py`, deterministic):
   - RFC 8650+ use the prepped v3 XML.
   - Older RFCs and all drafts use the text, with page headers and footers stripped and paragraphs rejoined across page breaks.
   - Headings are accepted only if their numbers run in sequence, so list items aren't mistaken for headings. Old RFCs with indented sub-headings get a second pass.
   - ASCII art, ABNF, protocol transcripts, header blocks and zone files keep their line breaks as figures.
   - Boilerplate sections are dropped. Bibliographies are stored but not embedded.
   - Output goes to `data/normalized/<doc>.json`.
4. **Chunk** (`chunk.py`): the parent is a whole section (in `sections`). Children are paragraph-packed chunks of about 1,000 chars. Figures and tables stay whole up to 1,800 chars (so the reranker sees all of them) and are split on line ends beyond that. Runs of base64 or hex are left out of the index; `get_section` still returns them. Each chunk is embedded with a context header, for example `RFC 2535 · Proposed Standard · OBSOLETED by 4033, 4034, 4035 · "…" · §4.1 …`. Each erratum is its own chunk, linked to its section.
5. **Embed**: Ollama `nomic-embed-text` (768-d).
6. **Search**: hybrid. pgvector cosine and Postgres full-text results are fused with reciprocal rank (RRF), and status filters go in the SQL. The top 20 sections are then re-scored by a cross-encoder (`bge-reranker-v2-m3` on a llama.cpp server).
7. **Graph**: `obsoletes`/`updates` come from the RFC index, STD/BCP membership from its `std-entry`/`bcp-entry` lists, and citation edges from each in-scope document's References sections (normative vs informative).

## MCP tools

| Tool | What it does |
|---|---|
| `search(query, k, include_obsolete, include_drafts, statuses, docs, topic)` | Hybrid search plus rerank. Defaults to current RFCs only, across all topics; `topic` is `dns` or `email` |
| `get_document(doc)` | Metadata, relations, STD/BCP membership, TOC, errata count, what it cites. `STD 13` returns both member RFCs |
| `cited_by(doc, current_only)` | Documents in the store whose References cite this one |
| `get_section(doc, section)` | Full section text, its subsections, and its errata |
| `lineage(doc)` | Follows obsoleted-by links to the current spec(s), with updated-by lists and pending drafts |
| `list_documents(status, wg, doc_type, current_only, title_contains, topic)` | Browse the corpus |
| `lookup_registry(query, registry)` | IANA code points (`65`, `HTTPS`, `ECDSAP256SHA256`, `5.7.1`, `STARTTLS`, …) with the defining RFCs' status |

Any tool taking a `doc` accepts `1035`, `RFC 1035`, `STD 13`, `BCP 91` or a draft name.

## Reranking

Measured on the combined DNS and email store, searching all topics:

| Eval | doc recall@5 | section recall@5 | doc MRR@10 | p50 latency |
|---|---|---|---|---|
| DNS (96), RRF only | 88/96 | 25/30 | 0.722 | 105 ms |
| DNS (96), + rerank, pool 20 (default) | 91/96 | 27/30 | 0.771 | 5.0 s |
| Email (64), RRF only | 58/64 | 25/26 | 0.734 | 107 ms |
| Email (64), + rerank, pool 20 (default) | 55/64 | 21/26 | 0.725 | 5.3 s |

The reranker helps DNS and hurts email. Restricting search to `topic=dns` barely changes the DNS numbers, so email isn't crowding out DNS results.

Pool size on the earlier DNS-only store (RRF 89/96, 25/30, 0.718): pool 12 gave 89/96, 26/30, 0.780; pool 20 gave 91/96, 27/30, 0.779; pool 30 gave 89/96, 28/30, 0.768.

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
