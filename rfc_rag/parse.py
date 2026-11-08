"""Deterministic parse of RFC/draft text or v3 XML into normalized sections.

Section shape: {id, title, depth, parent, embed, blocks: [{kind: text|figure|table, text}]}
"""

from __future__ import annotations

import re
import textwrap
import xml.etree.ElementTree as ET

# Boilerplate dropped entirely. References are kept but not embedded.
DROP = {
    "status of this memo", "copyright notice", "table of contents", "full copyright statement",
    "intellectual property", "authors' addresses", "author's address", "authors' address",
    "acknowledgements", "acknowledgments", "acknowledgement", "acknowledgment", "contributors", "index",
    "contents", "copyright statement",
}
UNNUMBERED = DROP | {"abstract", "references", "normative references", "informative references"}


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().rstrip(".").lower()


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _norm(title)).strip("-")


def _is_refs(title: str) -> bool:
    return "references" in _norm(title)


# --- Plain text ------------------------------------------------------------

PAGE_BREAK = re.compile(
    r"\n[ \t]*(?:\n[ \t]*)*\n[^\n]*\[Page \d+\][ \t]*\n\f?[ \t]*\n?"  # blank lines + footer + formfeed
    r"(?:(?:RFC \d+|Internet-Draft)[^\n]*\n)?"                        # running header
    r"(?:[ \t]*\n)*"                                                  # blank lines
)
HEADING = re.compile(r"^(\d+(?:\.\d+)*|Appendix [A-Z](?:\.\d+)*|[A-Z](?:\.\d+)+)\.?\s+(\S.{0,90})$")
TOC_LINE = re.compile(r"(?:\.\s?){3,}\s*\d+\s*$")
ART = re.compile(r"\+-{2,}|-{2,}\+|^\s*\|.*\|\s*$", re.M)


def _join_page(m: re.Match) -> str:
    """Rejoin text split across a page: mid-sentence gets one newline, else a paragraph break."""
    before = m.string[: m.start()].rstrip()
    after = m.string[m.end():].lstrip(" ")
    if before.endswith((".", ":")) and not after[:1].islower():
        return "\n\n"
    return "\n"


def strip_pages(text: str) -> str:
    text = text.replace("\r", "")
    text = PAGE_BREAK.sub(_join_page, text)
    return text.replace("\f", "")


def _key(num: str) -> tuple:
    num = num.removeprefix("Appendix ")
    return tuple(int(p) if p.isdigit() else p for p in num.split("."))


def _follows(prev: tuple | None, cur: tuple) -> bool:
    """True if cur plausibly comes next after prev: first child, or a sibling a little further on at some
    level (gaps up to 3 so one missed heading doesn't lock out the rest). Rejects list items like '1. foo'."""
    if prev is None:
        return cur[0] in (1, 2, "A") and all(p == 1 for p in cur[1:])
    if cur == prev + (1,):
        return True
    if cur == ("A",) and isinstance(prev[0], int):
        return True
    for i in range(len(prev)):
        a, b = prev[i], cur[i] if i < len(cur) else None
        if cur[:i] != prev[:i] or type(a) is not type(b):
            continue
        gap = b - a if isinstance(a, int) else ord(b) - ord(a)
        if 1 <= gap <= 3 and all(p == 1 for p in cur[i + 1 :]):
            return True
    return False


def _blocks(lines: list[str]) -> list[dict]:
    paras, cur = [], []
    for line in lines + [""]:
        if line.strip():
            cur.append(line.rstrip())
        elif cur:
            paras.append(cur)
            cur = []
    out: list[dict] = []
    for p in paras:
        raw = "\n".join(p)
        if ART.search(raw) or sum("|" in ln for ln in p) >= 2:
            text = textwrap.dedent(raw)
            if out and out[-1]["kind"] == "figure":  # figure with a blank line inside
                out[-1]["text"] += "\n\n" + text
            else:
                out.append({"kind": "figure", "text": text})
        else:
            out.append({"kind": "text", "text": " ".join(ln.strip() for ln in p)})
    return out


def parse_txt(text: str) -> list[dict]:
    lines = strip_pages(text).split("\n")
    heads = []  # (line index, id, title, depth, parent)
    last = None
    for i, line in enumerate(lines):
        if not line or line[0].isspace() or TOC_LINE.search(line):
            continue
        if _norm(line) in UNNUMBERED:
            heads.append((i, _slug(line), line.strip(), 1, None))
            continue
        m = HEADING.match(line)
        if not m:
            continue
        k = _key(m.group(1))
        if _follows(last, k):
            last = k
            sid = ".".join(str(p) for p in k)
            heads.append((i, sid, m.group(2).strip(), len(k), ".".join(str(p) for p in k[:-1]) or None))

    sections = []
    for n, (i, sid, title, depth, parent) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        if _norm(title) in DROP:
            continue
        sections.append({"id": sid, "title": title, "depth": depth, "parent": parent,
                         "embed": not _is_refs(title), "blocks": _blocks(lines[i + 1 : end])})
    if sum(len(b["text"]) for s in sections for b in s["blocks"]) < 0.2 * len(text):
        # ponytail: no usable numbered headings (some pre-1000 RFCs); keep the whole text as one section.
        body = "\n".join(lines)
        sections = [{"id": "body", "title": "Full text", "depth": 1, "parent": None, "embed": True,
                     "blocks": _blocks(body.split("\n"))}]
    # A child of a references section is also references.
    refs = {s["id"] for s in sections if not s["embed"]}
    for s in sections:
        if s["parent"] in refs:
            s["embed"] = False
    return sections


# --- v3 XML ----------------------------------------------------------------

BLOCKISH = {"t", "li", "dd", "dt", "ul", "ol", "dl", "aside", "blockquote", "figure", "table"}


def _inline(el) -> str:
    s = el.text or ""
    for c in el:
        if c.tag == "xref":
            dc = c.text or c.get("derivedContent") or c.get("target") or ""
            s += f"Section {c.get('section')} of {dc}" if c.get("section") else dc
        elif c.tag == "eref":
            s += c.text or c.get("target") or ""
        elif c.tag in ("artwork", "sourcecode"):
            s += "\n" + (c.text or "") + "\n"
        else:
            s += _inline(c) + (" " if c.tag in BLOCKISH else "")
        s += c.tail or ""
    return s


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _artwork(fig) -> str:
    arts = [a for a in fig.iter() if a.tag in ("artwork", "sourcecode")]
    pick = next((a for a in arts if a.get("type") != "svg" and (a.text or "").strip()), None)
    return textwrap.dedent((pick.text if pick is not None else "").strip("\n"))


def _xml_blocks(sec) -> list[dict]:
    out = []
    for c in sec:
        tag = c.tag
        if tag in ("name", "section", "references"):
            continue
        if tag in ("figure", "artwork", "sourcecode"):
            cap = c.findtext("name")
            art = _artwork(c) if tag == "figure" else textwrap.dedent((c.text or "").strip("\n"))
            if art:
                out.append({"kind": "figure", "text": (f"Figure: {_clean(cap)}\n" if cap else "") + art})
        elif tag == "table":
            rows = [" | ".join(_clean(_inline(cell)) for cell in tr) for tr in c.iter("tr")]
            out.append({"kind": "table", "text": "\n".join(rows)})
        elif tag in ("ul", "ol"):
            out.append({"kind": "text", "text": "\n".join("- " + _clean(_inline(li)) for li in c.findall("li"))})
        elif tag == "dl":
            items = list(c)
            pairs = [f"{_clean(_inline(a))}: {_clean(_inline(b))}" for a, b in zip(items[::2], items[1::2])]
            out.append({"kind": "text", "text": "\n".join(pairs)})
        elif tag == "reference":
            out.append({"kind": "text", "text": f"[{c.get('anchor')}] {_clean(c.findtext('front/title') or '')}"})
        else:  # t, aside, blockquote, note
            text = _clean(_inline(c))
            if text:
                out.append({"kind": "text", "text": text})
    return out


def _xml_id(sec) -> str:
    pn = (sec.get("pn") or "").removeprefix("section-")
    if sec.get("numbered") == "false" or not pn or pn.startswith(("boilerplate", "toc")):
        return _slug(sec.findtext("name") or pn)
    if pn.startswith("appendix."):
        parts = pn.removeprefix("appendix.").split(".")
        return ".".join([parts[0].upper()] + parts[1:])
    return pn


def _walk(sec, parent, depth, refs, out) -> None:
    title = _clean(_inline(sec.find("name"))) if sec.find("name") is not None else ""
    if _norm(title) in DROP:
        return
    sid = _xml_id(sec)
    refs = refs or sec.tag == "references" or _is_refs(title)
    out.append({"id": sid, "title": title, "depth": depth, "parent": parent,
                "embed": not refs, "blocks": _xml_blocks(sec)})
    for child in sec:
        if child.tag in ("section", "references"):
            _walk(child, sid, depth + 1, refs, out)


def parse_xml(data: bytes | str) -> list[dict]:
    root = ET.fromstring(data)
    out = []
    abstract = root.find("front/abstract")
    if abstract is not None:
        out.append({"id": "abstract", "title": "Abstract", "depth": 1, "parent": None,
                    "embed": True, "blocks": _xml_blocks(abstract)})
    for part in ("middle", "back"):
        el = root.find(part)
        for sec in (el if el is not None else []):
            if sec.tag in ("section", "references"):
                _walk(sec, None, 1, False, out)
    return out


def section_text(sec: dict) -> str:
    return "\n\n".join(b["text"] for b in sec["blocks"])
