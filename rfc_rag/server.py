"""MCP server over the RFC store (streamable HTTP on :8000/mcp)."""

from __future__ import annotations

import os
import re

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from rfc_rag import embed
from rfc_rag.chunk import label
from rfc_rag.db import connect

mcp = MCPServer(
    "rfc-rag",
    instructions=(
        "Search IETF DNS RFCs and active DNS Internet-Drafts. Every result carries its status. "
        "Prefer current documents (is_current=true), and cite as 'RFC N §X'. "
        "Internet-Drafts are work in progress, not standards. "
        "Use lineage() to find what replaced an obsolete RFC, and lookup_registry() for IANA code points."
    ),
)

RRF_K = 60


def norm_id(doc: str) -> str:
    """'1035' / 'rfc 1035' / 'RFC1035' -> 'RFC1035'; draft names pass through (minus any -NN revision)."""
    m = re.fullmatch(r"\s*(?:rfc\s*)?(\d+)\s*", doc, re.I)
    if m:
        return f"RFC{int(m.group(1))}"
    return re.sub(r"-\d\d$", "", doc.strip().lower())


def anchor(url: str, sid: str | None) -> str:
    if not sid or not re.match(r"^(\d|[A-Z](\.|$))", sid):
        return url
    return f"{url}#{'appendix' if sid[0].isalpha() else 'section'}-{sid}"


@mcp.tool()
def search(query: str, k: int = 8, include_obsolete: bool = False, include_drafts: bool = False,
           statuses: list[str] | None = None, docs: list[str] | None = None) -> list[dict]:
    """Hybrid (semantic + keyword) search over DNS RFC sections and errata.

    By default only current RFCs are searched: no obsoleted/Historic RFCs and no Internet-Drafts.
    statuses filters on RFC status, e.g. ["PROPOSED STANDARD", "INTERNET STANDARD", "BEST CURRENT PRACTICE"].
    docs limits the search to specific documents, e.g. ["RFC4034", "RFC4035"].
    """
    where, params = ["true"], {}
    if not include_obsolete:
        where.append("(d.is_current OR d.doc_type = 'draft')")
    if not include_drafts:
        where.append("d.doc_type = 'rfc'")
    if statuses:
        where.append("d.status = ANY(%(statuses)s)")
        params["statuses"] = [s.upper() for s in statuses]
    if docs:
        where.append("d.doc_id = ANY(%(docs)s)")
        params["docs"] = [norm_id(d) for d in docs]
    w = " AND ".join(where)
    params |= {"v": embed.vec(embed.embed([query], query=True)[0]), "t": query, "k": min(k, 30) * 3}
    sql = f"""
    WITH vec AS (
        SELECT c.chunk_id, row_number() OVER (ORDER BY c.embedding <=> %(v)s::vector) AS r
        FROM chunks c JOIN documents d USING (doc_id) WHERE {w}
        ORDER BY c.embedding <=> %(v)s::vector LIMIT 50),
    lex AS (
        SELECT c.chunk_id, row_number() OVER (ORDER BY ts_rank_cd(c.tsv, q) DESC) AS r
        FROM chunks c JOIN documents d USING (doc_id), websearch_to_tsquery('english', %(t)s) q
        WHERE c.tsv @@ q AND {w}
        ORDER BY ts_rank_cd(c.tsv, q) DESC LIMIT 50),
    fused AS (
        SELECT chunk_id, sum(1.0 / ({RRF_K} + r)) AS score
        FROM (SELECT * FROM vec UNION ALL SELECT * FROM lex) u GROUP BY chunk_id
        ORDER BY score DESC LIMIT %(k)s)
    SELECT c.doc_id, c.section_id, s.title AS section_title, c.kind, c.content, f.score,
           d.title, d.status, d.is_current, d.obsoleted_by, d.updated_by, d.url,
           EXISTS (SELECT 1 FROM errata e WHERE e.doc_id = c.doc_id AND e.section_id = c.section_id) AS has_errata
    FROM fused f JOIN chunks c USING (chunk_id) JOIN documents d USING (doc_id)
    LEFT JOIN sections s ON s.doc_id = c.doc_id AND s.section_id = c.section_id
    ORDER BY f.score DESC"""
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    seen, out = set(), []
    for r in rows:  # one hit per section, so a long section can't take every slot
        key = (r["doc_id"], r["section_id"], r["kind"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    rows = out[: min(k, 30)]
    for r in rows:
        r["citation"] = f"{label(r['doc_id'])} §{r['section_id']}" if r["section_id"] else label(r["doc_id"])
        r["url"] = anchor(r["url"], r["section_id"])
        r["score"] = round(float(r["score"]), 4)
    return rows


@mcp.tool()
def get_document(doc: str) -> dict:
    """Metadata, status, obsoletes/updates relations and table of contents for an RFC or draft."""
    doc = norm_id(doc)
    with connect() as conn:
        d = conn.execute("SELECT * FROM documents WHERE doc_id = %s", (doc,)).fetchone()
        if not d:
            return {"error": f"{doc} not found"}
        d["toc"] = [f"{'  ' * (r['depth'] - 1)}{r['section_id']} {r['title']}" for r in conn.execute(
            "SELECT section_id, title, depth FROM sections WHERE doc_id = %s ORDER BY ord", (doc,))]
        d["errata_count"] = conn.execute("SELECT count(*) AS n FROM errata WHERE doc_id = %s", (doc,)).fetchone()["n"]
    return d


@mcp.tool()
def get_section(doc: str, section: str) -> dict:
    """Full text of one section (e.g. doc='RFC1035', section='4.1.1'), its subsection titles, and its errata."""
    doc, section = norm_id(doc), section.strip().removeprefix("§").rstrip(".")
    with connect() as conn:
        s = conn.execute("SELECT s.*, d.status, d.is_current, d.url FROM sections s JOIN documents d USING (doc_id)"
                         " WHERE doc_id = %s AND section_id = %s", (doc, section)).fetchone()
        if not s:
            return {"error": f"{doc} §{section} not found; see get_document('{doc}') for the TOC"}
        s["subsections"] = [f"{r['section_id']} {r['title']}" for r in conn.execute(
            "SELECT section_id, title FROM sections WHERE doc_id = %s AND parent_id = %s ORDER BY ord", (doc, section))]
        s["errata"] = conn.execute("SELECT errata_id, type, status, original, corrected, notes FROM errata"
                                   " WHERE doc_id = %s AND section_id = %s", (doc, section)).fetchall()
        s["url"] = anchor(s["url"], section)
    return s


@mcp.tool()
def lineage(doc: str) -> dict:
    """Follow obsoleted-by links forward to the current spec(s), listing what updates each one,
    plus active Internet-Drafts that propose to update or obsolete them."""
    start = norm_id(doc)
    nodes, seen, queue = [], set(), [start]
    with connect() as conn:
        while queue:
            cur = queue.pop(0)
            if cur in seen:
                continue
            seen.add(cur)
            d = conn.execute("SELECT doc_id, title, status, is_current, pub_date, obsoletes, obsoleted_by, updates,"
                             " updated_by FROM documents WHERE doc_id = %s", (cur,)).fetchone()
            if not d:
                continue
            d["pending_drafts"] = [r["doc_id"] for r in conn.execute(
                "SELECT doc_id FROM documents WHERE doc_type = 'draft' AND (updates @> ARRAY[%s] OR obsoletes @> ARRAY[%s])",
                (cur, cur))]
            nodes.append(d)
            queue += d["obsoleted_by"]
    if not nodes:
        return {"error": f"{start} not found"}
    return {"doc": start, "current": [n["doc_id"] for n in nodes if n["is_current"] and not n["obsoleted_by"]],
            "chain": nodes}


@mcp.tool()
def list_documents(status: str | None = None, wg: str | None = None, doc_type: str | None = None,
                   current_only: bool = True, title_contains: str | None = None, limit: int = 100) -> list[dict]:
    """List DNS documents in the store, filtered by status, working group, doc_type ('rfc'|'draft') or title."""
    where, p = ["in_scope"], {"limit": min(limit, 500)}
    if current_only:
        where.append("(is_current OR doc_type = 'draft')")
    for col, val in (("status", status and status.upper()), ("wg", wg and wg.lower()), ("doc_type", doc_type)):
        if val:
            where.append(f"{col} = %({col})s")
            p[col] = val
    if title_contains:
        where.append("title ILIKE %(t)s")
        p["t"] = f"%{title_contains}%"
    with connect() as conn:
        return conn.execute(f"SELECT doc_id, title, status, is_current, wg, pub_date FROM documents"
                            f" WHERE {' AND '.join(where)} ORDER BY doc_type, length(doc_id), doc_id LIMIT %(limit)s",
                            p).fetchall()


@mcp.tool()
def lookup_registry(query: str, registry: str | None = None) -> list[dict]:
    """Look up IANA DNS registries (RR TYPEs, CLASSes, OpCodes, RCODEs, EDNS options, DNSSEC algorithms, ...)
    by value or name, e.g. '65', 'HTTPS', 'ECDSAP256SHA256', 'BADCOOKIE'. Returns defining RFCs with their status."""
    q = query.strip()
    p = {"q": q, "like": f"%{q}%", "reg": f"%{registry or ''}%"}
    with connect() as conn:
        rows = conn.execute(
            """SELECT registry, value, name, description, refs,
                      (value = %(q)s OR lower(name) = lower(%(q)s)) AS exact
               FROM iana WHERE registry ILIKE %(reg)s
                 AND (value = %(q)s OR name ILIKE %(like)s OR description ILIKE %(like)s)
               ORDER BY exact DESC, registry LIMIT 25""", p).fetchall()
        refs = sorted({r for row in rows for r in row["refs"]})
        status = {r["doc_id"]: r for r in conn.execute(
            "SELECT doc_id, title, status, is_current, obsoleted_by FROM documents WHERE doc_id = ANY(%s)", (refs,))}
    for row in rows:
        row["refs"] = [status.get(r, {"doc_id": r}) for r in row["refs"]]
    return rows


if __name__ == "__main__":
    mcp.run(
        "streamable-http",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        # DNS-rebinding protection stays on even though we bind 0.0.0.0 inside the container.
        transport_security=TransportSecuritySettings(
            allowed_hosts=["localhost:*", "127.0.0.1:*"],
            allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
        ),
    )
