"""Sections -> embeddable chunks with a contextual header."""

from __future__ import annotations

import re

SIZE = 1000      # chars per child chunk
OVERLAP = 150    # only used when a single paragraph is longer than SIZE
FIG_CAP = 1800   # figures up to this stay whole: fits the reranker's 2000 chars with the context header
BLOB = re.compile(r"(?:^[ \t]*[A-Za-z0-9+/=]{40,}[ \t]*(?:\n|\Z)){4,}", re.M)  # armored keys, base64 bodies, hex dumps


def label(doc_id: str) -> str:
    return f"RFC {doc_id[3:]}" if doc_id.startswith("RFC") else doc_id


def _rfcs(ids: list[str], cap: int = 6) -> str:
    s = ", ".join(i[3:] for i in ids[:cap])
    return s + (f" +{len(ids) - cap} more" if len(ids) > cap else "")


def context(meta: dict, sec_id: str | None, sec_title: str | None) -> str:
    if meta["doc_type"] != "draft":
        status = meta["status"].title()
    elif meta["stream"] == "individual":
        status = "Individual Internet-Draft (proposal, not WG-adopted)"
    else:
        status = "Internet-Draft (work in progress)"
    parts = [label(meta["doc_id"]), status]
    if meta["obsoleted_by"]:
        parts.append(f"OBSOLETED by {_rfcs(meta['obsoleted_by'])}")
    if meta["updated_by"]:
        parts.append(f"updated by {_rfcs(meta['updated_by'])}")
    parts.append(f'"{meta["title"]}"')
    if sec_id:
        parts.append(f"§{sec_id} {sec_title}")
    return " · ".join(parts)


def split_to_size(text: str, size: int = SIZE, overlap: int = OVERLAP) -> list[str]:
    """Ported from carefortis-chunking-layer: cut at the last paragraph/line/sentence/word break."""
    if len(text) <= size:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        piece = text[start:end]
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                idx = piece.rfind(sep)
                if idx > int(size * 0.6):
                    end = start + idx + len(sep)
                    piece = text[start:end]
                    break
        parts.append(piece.strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [p for p in parts if p]


def pack(blocks: list[dict], size: int = SIZE) -> list[str]:
    """Greedy-pack paragraph blocks into chunks. Figures and tables stay whole up to FIG_CAP.
    Encoded data is left out of the index; get_section still returns it."""
    pieces = []
    for b in blocks:
        text = BLOB.sub("[encoded data omitted]\n", b["text"])
        pieces += split_to_size(text, size) if b["kind"] == "text" else split_to_size(text, FIG_CAP, 0)
    out, cur = [], ""
    for p in pieces:
        if cur and len(cur) + 2 + len(p) > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


def build(meta: dict, sections: list[dict], errata: list[dict]) -> list[dict]:
    doc = meta["doc_id"]
    chunks = []
    for sec in sections:
        if not sec["embed"]:
            continue
        ctx = context(meta, sec["id"], sec["title"])
        for i, text in enumerate(pack(sec["blocks"])):
            chunks.append({"chunk_id": f"{doc}#{sec['id']}#{i}", "section_id": sec["id"], "kind": "text",
                           "context": ctx, "content": text})
    titles = {s["id"]: s["title"] for s in sections}
    for e in errata:
        sid = e["section_id"]
        body = (f"Erratum {e['errata_id']} ({e['status']}, {e['type']})\n"
                f"Original:\n{e['original'] or ''}\nCorrected:\n{e['corrected'] or ''}\nNotes: {e['notes'] or ''}")
        chunks.append({"chunk_id": f"{doc}#errata-{e['errata_id']}", "section_id": sid, "kind": "erratum",
                       "context": "ERRATUM · " + context(meta, sid, titles.get(sid)),
                       "content": split_to_size(body, 4000)[0]})
    for n, c in enumerate(chunks):
        c["ord"] = n
    return chunks


if __name__ == "__main__":
    blocks = [{"kind": "text", "text": "a" * 600}, {"kind": "figure", "text": "+--+\n|ID|\n+--+" * 100},
              {"kind": "text", "text": "word " * 500}]
    out = pack(blocks)
    assert any("+--+\n|ID|" * 1 in c and len(c) > SIZE for c in out), "figure kept whole"
    assert all(len(c) <= SIZE for c in out if "+--+" not in c)
    big = pack([{"kind": "figure", "text": "rule = a / b\n" * 500}])
    assert len(big) > 1 and all(len(c) <= FIG_CAP for c in big) and big[0].count("\n") > 50, "huge figure split on lines"
    blob = pack([{"kind": "figure", "text": "-----BEGIN KEY-----\n" + ("QUJD" * 16 + "\n") * 300 + "=abcd\n-----END KEY-----"}])
    assert len(blob) == 1 and "omitted" in blob[0] and "QUJD" not in blob[0], "encoded data dropped"
    meta = {"doc_id": "RFC2535", "doc_type": "rfc", "status": "PROPOSED STANDARD", "title": "DNSSEC",
            "obsoleted_by": ["RFC4033", "RFC4034", "RFC4035"], "updated_by": []}
    assert context(meta, "4.1", "SIG").startswith("RFC 2535 · Proposed Standard · OBSOLETED by 4033, 4034, 4035")
    print("ok")
