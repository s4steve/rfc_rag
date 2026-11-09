"""fetch -> parse -> chunk -> embed -> load. Idempotent: unchanged chunks are not re-embedded."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from rfc_rag import chunk, embed, fetch, parse
from rfc_rag.db import connect

NORMALIZED = Path("data/normalized")
SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"

DOC_COLS = ["doc_id", "doc_type", "title", "status", "stream", "wg", "area", "pub_date", "abstract",
            "obsoletes", "obsoleted_by", "updates", "updated_by", "in_scope", "draft_rev", "draft_state",
            "expires", "url", "also", "topics"]


def upsert_documents(cur, docs: list[dict]) -> None:
    cols = ", ".join(DOC_COLS)
    sets = ", ".join(f"{c} = excluded.{c}" for c in DOC_COLS[1:])
    cur.executemany(
        f"INSERT INTO documents ({cols}) VALUES ({', '.join('%(' + c + ')s' for c in DOC_COLS)}) "
        f"ON CONFLICT (doc_id) DO UPDATE SET {sets}",
        docs,
    )


def load_errata(cur, rows: list[dict], sections: dict[str, set[str]], known: set[str]) -> dict[str, list]:
    cur.execute("TRUNCATE errata")
    by_doc: dict[str, list] = {}
    for e in rows:
        doc = e["doc-id"]
        if doc not in known:
            continue
        sid = fetch.errata_section(e["section"])
        r = {"errata_id": e["errata_id"], "doc_id": doc,
             "section_id": sid if sid in sections.get(doc, ()) else None,
             "type": e["errata_type_code"], "status": e["errata_status_code"],
             "original": e["orig_text"], "corrected": e["correct_text"], "notes": e["notes"]}
        by_doc.setdefault(doc, []).append(r)
    cur.executemany(
        "INSERT INTO errata VALUES (%(errata_id)s, %(doc_id)s, %(section_id)s, %(type)s, %(status)s,"
        " %(original)s, %(corrected)s, %(notes)s)",
        [r for rs in by_doc.values() for r in rs],
    )
    return by_doc


def store_doc(conn, meta: dict, sections: list[dict], errata: list[dict]) -> int:
    """Replace a document's sections and chunks. Returns number of chunks embedded."""
    doc = meta["doc_id"]
    chunks = chunk.build(meta, sections, errata)
    for c in chunks:
        c["content_hash"] = hashlib.sha256(f"{embed.MODEL}\n{c['context']}\n{c['content']}".encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT chunk_id, content_hash, embedding::text AS e FROM chunks WHERE doc_id = %s", (doc,))
        old = {r["chunk_id"]: r for r in cur.fetchall()}
        if {k: v["content_hash"] for k, v in old.items()} == {c["chunk_id"]: c["content_hash"] for c in chunks}:
            return 0
        todo = [c for c in chunks if old.get(c["chunk_id"], {}).get("content_hash") != c["content_hash"]]
        for c, v in zip(todo, embed.embed([f"{c['context']}\n\n{c['content']}" for c in todo])):
            c["embedding"] = embed.vec(v)
        for c in chunks:
            c.setdefault("embedding", old.get(c["chunk_id"], {}).get("e"))
            c.update(doc_id=doc, model=embed.MODEL)
        cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc,))
        cur.execute("DELETE FROM sections WHERE doc_id = %s", (doc,))
        cur.executemany(
            "INSERT INTO sections VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(doc, s["id"], s["parent"], s["title"], s["depth"], n, parse.section_text(s))
             for n, s in enumerate(sections)],
        )
        cur.executemany(
            "INSERT INTO chunks (chunk_id, doc_id, section_id, kind, ord, context, content, content_hash, model,"
            " embedding) VALUES (%(chunk_id)s, %(doc_id)s, %(section_id)s, %(kind)s, %(ord)s, %(context)s,"
            " %(content)s, %(content_hash)s, %(model)s, %(embedding)s::vector)",
            chunks,
        )
    conn.commit()
    return len(todo)


def parse_doc(meta: dict) -> list[dict]:
    if meta["doc_type"] == "draft":
        return parse.parse_txt(fetch.draft_source(meta).read_text(errors="replace"))
    path, ext = fetch.rfc_source(meta["doc_id"])
    return parse.parse_xml(path.read_bytes()) if ext == "xml" else parse.parse_txt(path.read_text(errors="replace"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-download index, errata, IANA and draft lists")
    ap.add_argument("--only", help="comma-separated doc ids to (re)process, e.g. RFC1035,RFC9460")
    args = ap.parse_args()

    embed.ensure_model()
    docs = fetch.load_index(args.refresh)
    fetch.mark_scope(docs)
    drafts = fetch.load_drafts(args.refresh)
    for d in drafts.values():
        try:
            d.update(fetch.draft_relations(fetch.draft_source(d).read_text(errors="replace")))
        except Exception as e:
            print(f"  no text for {d['doc_id']}: {e}", flush=True)
    docs |= drafts
    for d in docs.values():
        d.setdefault("also", [])

    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA.read_text())  # idempotent migrations
        upsert_documents(cur, list(docs.values()))
        cur.execute("DELETE FROM documents WHERE doc_type = 'draft' AND NOT doc_id = ANY(%s)", (list(drafts),))
        cur.execute("TRUNCATE iana")
        cur.executemany("INSERT INTO iana VALUES (%(registry)s, %(value)s, %(name)s, %(description)s, %(refs)s)",
                        fetch.load_iana(args.refresh))
        conn.commit()

        todo = sorted(d for d, m in docs.items() if m["in_scope"])
        if args.only:
            todo = [d for d in args.only.split(",") if d in docs]
        print(f"{len(docs)} documents, {len(todo)} to process", flush=True)

        parsed = {}
        for doc in todo:
            try:
                parsed[doc] = parse_doc(docs[doc])
            except Exception as e:  # e.g. RFC not issued, or no text format: keep metadata only
                print(f"  skip {doc}: {e}", flush=True)
                continue
            NORMALIZED.mkdir(parents=True, exist_ok=True)
            (NORMALIZED / f"{doc}.json").write_text(json.dumps({"id": doc, "sections": parsed[doc]}, indent=1))

        cur.execute("SELECT doc_id, section_id FROM sections")
        known_sections: dict[str, set[str]] = {}
        for r in cur.fetchall():
            known_sections.setdefault(r["doc_id"], set()).add(r["section_id"])
        known_sections |= {d: {s["id"] for s in secs} for d, secs in parsed.items()}
        errata = load_errata(cur, fetch.load_errata(args.refresh), known_sections, set(docs))
        conn.commit()

        for n, (doc, secs) in enumerate(parsed.items(), 1):
            try:
                cur.execute("DELETE FROM cites WHERE src = %s", (doc,))
                cur.executemany("INSERT INTO cites VALUES (%s, %s, %s)",
                                [(doc, dst, kind) for dst, kind in parse.citations(secs, doc)])
                conn.commit()
                k = store_doc(conn, docs[doc], secs, errata.get(doc, []))
            except Exception as e:  # one bad document shouldn't cost the rest of a long embedding run
                conn.rollback()
                print(f"[{n}/{len(parsed)}] {doc}: FAILED {e!r}", flush=True)
                continue
            print(f"[{n}/{len(parsed)}] {doc}: {len(secs)} sections, {k} embedded", flush=True)


if __name__ == "__main__":
    main()
