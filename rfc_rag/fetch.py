"""Download and cache the public sources, and read their metadata."""

from __future__ import annotations

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

RAW = Path("data/raw")
UA = {"User-Agent": "rfc-rag/0.1 (personal research index)"}

# DNS scope.
DNS_WGS = {"dnsop", "dnsext", "dnsind", "dnssd", "dprive", "add", "dance", "deleg", "dns", "dnssec"}
DRAFT_WGS = ["dnsop", "dprive", "add", "dance", "deleg", "dnssd"]  # WGs that are still open
SEED = {"RFC1034", "RFC1035", "RFC1123", "RFC2181", "RFC2308", "RFC3596", "RFC6891", "RFC9460"}
TITLE_RX = re.compile(r"\bDNS|Domain Name|DNSSEC", re.I)

IANA_FILES = {
    "dns-parameters.xml": "https://www.iana.org/assignments/dns-parameters/dns-parameters.xml",
    "dns-sec-alg-numbers.xml": "https://www.iana.org/assignments/dns-sec-alg-numbers/dns-sec-alg-numbers.xml",
}


def get(url: str, name: str, refresh: bool = False) -> Path:
    """Download url to data/raw/name once; refresh=True re-downloads."""
    path = RAW / name
    if path.exists() and not refresh:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                data = r.read()
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


# --- RFC index -------------------------------------------------------------

NS = {"r": "https://www.rfc-editor.org/rfc-index"}


def _ids(entry, tag: str) -> list[str]:
    return [d.text for d in entry.findall(f"r:{tag}/r:doc-id", NS) if d.text.startswith("RFC")]


def load_index(refresh: bool = False) -> dict[str, dict]:
    root = ET.parse(get("https://www.rfc-editor.org/rfc-index.xml", "rfc-index.xml", refresh)).getroot()
    docs = {}
    for e in root.findall("r:rfc-entry", NS):
        doc_id = e.findtext("r:doc-id", namespaces=NS)
        num = int(doc_id[3:])
        docs[doc_id] = {
            "doc_id": doc_id,
            "doc_type": "rfc",
            "title": e.findtext("r:title", namespaces=NS),
            "status": e.findtext("r:current-status", namespaces=NS),
            "stream": e.findtext("r:stream", namespaces=NS),
            "wg": e.findtext("r:wg_acronym", namespaces=NS),
            "area": e.findtext("r:area", namespaces=NS),
            "pub_date": f"{e.findtext('r:date/r:month', namespaces=NS)} {e.findtext('r:date/r:year', namespaces=NS)}",
            "abstract": " ".join("".join(p.itertext()) for p in e.findall("r:abstract/r:p", NS)) or None,
            "obsoletes": _ids(e, "obsoletes"),
            "obsoleted_by": _ids(e, "obsoleted-by"),
            "updates": _ids(e, "updates"),
            "updated_by": _ids(e, "updated-by"),
            "in_scope": False,
            "draft_rev": None,
            "draft_state": None,
            "expires": None,
            "url": f"https://www.rfc-editor.org/rfc/rfc{num}",
        }
    return docs


def mark_scope(docs: dict[str, dict]) -> None:
    """DNS scope: DNS WGs + seeds + title match, then one hop over obsoletes/updates edges."""
    core = {d for d, m in docs.items() if m["wg"] in DNS_WGS or d in SEED or TITLE_RX.search(m["title"])}
    hop = set()
    for d in core:
        m = docs[d]
        hop.update(m["obsoletes"] + m["obsoleted_by"] + m["updates"] + m["updated_by"])
    for d in core | hop:
        if d in docs:
            docs[d]["in_scope"] = True


def rfc_source(doc_id: str) -> tuple[Path, str]:
    """RFC 8650+ are published as prepped v3 XML; older ones as text."""
    num = int(doc_id[3:])
    ext = "xml" if num >= 8650 else "txt"
    return get(f"https://www.rfc-editor.org/rfc/rfc{num}.{ext}", f"rfc/rfc{num}.{ext}"), ext


# --- Errata ----------------------------------------------------------------

KEEP_ERRATA = {"Verified", "Held for Document Update"}


def load_errata(refresh: bool = False) -> list[dict]:
    rows = json.loads(get("https://www.rfc-editor.org/errata.json", "errata.json", refresh).read_text())
    return [e for e in rows if e["errata_status_code"] in KEEP_ERRATA]


def errata_section(raw: str | None) -> str | None:
    """'4.1.1' / 'Section 4.1.1' / 'Appendix A.2' / '4.1.1.' -> '4.1.1' / 'A.2'; 'GLOBAL' -> None."""
    m = re.fullmatch(r"\s*(?:section\s+|appendix\s+)?(\d+|[A-Z])((?:\.\d+)*)\.?\s*", raw or "", re.I)
    return m.group(1).upper() + m.group(2) if m else None


# --- IANA ------------------------------------------------------------------

IA = "{http://www.iana.org/assignments}"


def load_iana(refresh: bool = False) -> list[dict]:
    rows = []
    for name, url in IANA_FILES.items():
        root = ET.parse(get(url, f"iana/{name}", refresh)).getroot()
        for reg in root.iter(f"{IA}registry"):
            title = reg.findtext(f"{IA}title")
            for rec in reg.findall(f"{IA}record"):
                t = lambda tag: (rec.findtext(f"{IA}{tag}") or "").strip() or None  # noqa: E731
                refs = [x.get("data").upper() for x in rec.findall(f"{IA}xref") if x.get("type") in ("rfc", "draft")]
                rows.append({
                    "registry": title,
                    "value": t("value") or t("number") or t("code"),
                    "name": t("type") or t("mnemonic") or t("name") or t("description"),
                    "description": t("description") or t("meaning"),
                    "refs": [r if r.startswith("RFC") else r.lower() for r in refs],
                })
    return rows


# --- Internet-Drafts -------------------------------------------------------

DT = "https://datatracker.ietf.org"


def load_drafts(refresh: bool = False) -> dict[str, dict]:
    docs = {}
    for wg in DRAFT_WGS:
        url = f"{DT}/api/v1/doc/document/?type=draft&group__acronym={wg}&states__slug=active&format=json&limit=200"
        for o in json.loads(get(url, f"drafts/{wg}.json", refresh).read_text())["objects"]:
            docs[o["name"]] = {
                "doc_id": o["name"],
                "doc_type": "draft",
                "title": o["title"],
                "status": "INTERNET-DRAFT",
                "stream": "IETF",
                "wg": wg,
                "area": None,
                "pub_date": o["time"][:10],
                "abstract": o["abstract"].strip(),
                "obsoletes": [],
                "obsoleted_by": [],
                "updates": [],
                "updated_by": [],
                "in_scope": True,
                "draft_rev": o["rev"],
                "draft_state": "active",
                "expires": (o["expires"] or "")[:10] or None,
                "url": f"{DT}/doc/{o['name']}/",
            }
    return docs


def draft_source(d: dict) -> Path:
    # ponytail: always .txt. Submitted draft XML is not "prepped", so it has no section numbers.
    name = f"{d['doc_id']}-{d['draft_rev']}.txt"
    return get(f"https://www.ietf.org/archive/id/{name}", f"drafts/{name}")


def draft_relations(text: str) -> dict[str, list[str]]:
    """'Updates: 1035, 2181 (if approved)' in the draft header -> {'updates': ['RFC1035', 'RFC2181']}."""
    out = {"updates": [], "obsoletes": []}
    head = text[:3000]
    for key in out:
        m = re.search(rf"^{key}:\s*([\d,\s]+?)\s*(?:\(if approved\))?(?:\s{{2,}}|$)", head, re.I | re.M)
        if m:
            out[key] = [f"RFC{n}" for n in re.findall(r"\d+", m.group(1))]
    return out
