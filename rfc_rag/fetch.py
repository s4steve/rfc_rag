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

# What each topic covers. A document can belong to several (MX, SPF, DANE for SMTP).
TOPICS = {
    "dns": {
        "wgs": {"dnsop", "dnsext", "dnsind", "dnssd", "dprive", "add", "dance", "deleg", "dns", "dnssec"},
        "seeds": {"RFC1034", "RFC1035", "RFC1123", "RFC2181", "RFC2308", "RFC3596", "RFC6891", "RFC9460"},
        "title": re.compile(r"\bDNS|Domain Name|DNSSEC", re.I),
        "title_skip": None,
        "title_skip_status": set(),
        "skip_wgs": set(),
        "hop": ("obsoletes", "obsoleted_by", "updates", "updated_by"),
        "draft_wgs": ["dnsop", "dprive", "add", "dance", "deleg", "dnssd"],  # WGs that are still open
        "draft_terms": [],
        "draft_tokens": set(),
    },
    "email": {
        "wgs": {"drums", "emailcore", "smtpext", "822ext", "imap", "imapext", "extra", "morg", "lemonade", "eai", "jmap",
                "mailmaint", "sieve", "msgtrk", "dkim", "dmarc", "spfbis", "smime", "openpgp"},
        "seeds": {f"RFC{n}" for n in (
            821, 822, 1123, 1939, 2045, 2046, 2047, 2048, 2049, 2183, 3207, 3461, 3462, 3463, 3464, 5321, 5322, 6186,
            6409, 6522, 7505, 7672, 8314, 8460, 8461, 8601, 9989, 9990, 9991)},
        # No bare 'SPF' here: in titles it is usually Shortest Path First.
        "title": re.compile(r"\b(e-?mail|mail|SMTP|ESMTP|IMAP4?|POP3|MIME|S/MIME|OpenPGP|Sieve|JMAP|DKIM|DMARC"
                            r"|Sender Policy|Internet Message|Message Submission|Message Disposition|Delivery Status)\b", re.I),
        # Scope excludes gateways, fax/voice mail, and the many "MIME type" registrations that aren't about mail.
        "title_skip": re.compile(r"X\.400|X\.500|facsimile|\bfax\b|\bVPIM\b|voice|Privacy.Enhance"
                                 r"|MIME (sub-?)?types?|Media Types?\b|application/|\bMIXER\b", re.I),
        # 1970s-80s notes and meeting minutes have no status, so they would otherwise count as current.
        "title_skip_status": {"UNKNOWN"},
        # Mention MIME/mail in the title but aren't about email.
        "skip_wgs": {"fax", "vpim", "trade", "pem", "x400ops", "mixer", "edi", "ediint", "sip", "simple", "asid", "find",
                     "schema", "conneg", "opes", "regext", "tls"},
        # ponytail: lineage only. Following updates edges too pulled in SIP and X.509 documents.
        "hop": ("obsoletes", "obsoleted_by"),
        "draft_wgs": [],  # WG drafts have their WG in the name, so draft_terms finds them as well
        "draft_terms": ["mail", "smtp", "imap", "dkim", "dmarc", "mime", "jmap", "openpgp"],
        # Whole hyphen-separated tokens, so 'simap' and 'mailschema' don't count. spf/arc are left out: nearly
        # every active draft containing them is about OSPF/BGP-SPF or an "architecture".
        "draft_tokens": {"email", "mail", "emailcore", "mailmaint", "smtp", "smtputf8", "imap", "dkim", "dkim2",
                         "dmarc", "mime", "smime", "jmap", "openpgp"},
    },
}

_IANA = "https://www.iana.org/assignments"
IANA_FILES = {
    "dns-parameters.xml": f"{_IANA}/dns-parameters/dns-parameters.xml",
    "dns-sec-alg-numbers.xml": f"{_IANA}/dns-sec-alg-numbers/dns-sec-alg-numbers.xml",
    "smtp.xml": f"{_IANA}/smtp/smtp.xml",
    "smtp-enhanced-status-codes.xml": f"{_IANA}/smtp-enhanced-status-codes/smtp-enhanced-status-codes.xml",
    "message-headers.xml": f"{_IANA}/message-headers/message-headers.xml",
    "imap-capabilities.xml": f"{_IANA}/imap-capabilities/imap-capabilities.xml",
    "dkim-parameters.xml": f"{_IANA}/dkim-parameters/dkim-parameters.xml",
    "dmarc-parameters.xml": f"{_IANA}/dmarc-parameters/dmarc-parameters.xml",
    "email-auth.xml": f"{_IANA}/email-auth/email-auth.xml",
    "sieve-extensions.xml": f"{_IANA}/sieve-extensions/sieve-extensions.xml",
    "jmap.xml": f"{_IANA}/jmap/jmap.xml",
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
            "topics": [],
            "draft_rev": None,
            "draft_state": None,
            "expires": None,
            "url": f"https://www.rfc-editor.org/rfc/rfc{num}",
            "also": [],
        }
    add_subseries(root, docs)
    return docs


def add_subseries(root, docs: dict[str, dict]) -> None:
    """STD/BCP membership from <std-entry>/<bcp-entry>: RFC1035.also = ['STD13']."""
    for tag in ("std-entry", "bcp-entry"):
        for e in root.findall(f"r:{tag}", NS):
            alias = re.sub(r"^([A-Z]+)0*(\d)", r"\1\2", e.findtext("r:doc-id", namespaces=NS))
            for rfc in _ids(e, "is-also"):
                if rfc in docs:
                    docs[rfc]["also"].append(alias)


def mark_scope(docs: dict[str, dict]) -> None:
    """Each topic: its WGs + seeds + title match, then one hop over that topic's relation edges."""
    for topic, cfg in TOPICS.items():
        core = {d for d, m in docs.items()
                if m["wg"] in cfg["wgs"] or d in cfg["seeds"]
                or (cfg["title"].search(m["title"]) and m["wg"] not in cfg["skip_wgs"]
                    and m["status"] not in cfg["title_skip_status"]
                    and not (cfg["title_skip"] and cfg["title_skip"].search(m["title"])))}
        hop = {x for d in core for edge in cfg["hop"] for x in docs[d][edge]}
        for d in core | hop:
            if d in docs:
                docs[d]["in_scope"] = True
                docs[d]["topics"].append(topic)


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
        try:
            root = ET.parse(get(url, f"iana/{name}", refresh)).getroot()
        except Exception as e:  # one moved registry shouldn't stop an ingest
            print(f"  skip IANA {name}: {e}", flush=True)
            continue
        for reg in root.iter(f"{IA}registry"):
            title = reg.findtext(f"{IA}title")
            for rec in reg.findall(f"{IA}record"):
                t = lambda tag: (rec.findtext(f"{IA}{tag}") or "").strip() or None  # noqa: E731
                # iter, not findall: some email registries nest the xref inside <reference> or <rfc>.
                refs = {_iana_ref(x) for x in rec.iter(f"{IA}xref") if x.get("type") in ("rfc", "draft")}
                nm = t("type") or t("mnemonic") or t("name") or t("sample") or t("summary")
                if t("method"):  # Email Authentication Methods: 'dkim' + 'header.d'
                    nm = f"{t('method')} {t('ptype')}.{nm}"
                rows.append({
                    "registry": title,
                    "value": t("value") or t("number") or t("code"),
                    "name": nm or t("description") or t("value"),
                    "description": t("description") or t("meaning"),
                    "refs": sorted(refs),
                })
    return rows


def _iana_ref(x) -> str:
    """xref data -> doc id: 'rfc6376' -> 'RFC6376'; 'RFC-ietf-emailcore-rfc5321bis-43' (in the RFC Editor queue)
    and 'draft-foo-bar-03' -> their draft name."""
    data = x.get("data")
    if x.get("type") == "rfc":
        return data.upper()
    return re.sub(r"-\d\d$", "", re.sub(r"^RFC-", "draft-", data)).lower()


# --- Internet-Drafts -------------------------------------------------------

DT = "https://datatracker.ietf.org"


def _active_drafts(query: str, cache: str, refresh: bool) -> list[dict]:
    """Every page of a Datatracker active-draft query."""
    url, page, out = f"{DT}/api/v1/doc/document/?type=draft&{query}&states__slug=active&format=json&limit=200", 0, []
    while url:
        res = json.loads(get(url, f"drafts/{cache}{f'-p{page}' if page else ''}.json", refresh).read_text())
        out += res["objects"]
        url, page = DT + res["meta"]["next"] if res["meta"]["next"] else None, page + 1
    return out


def wants_draft(name: str, tokens: set[str]) -> bool:
    return bool(tokens & set(name.split("-")))


def load_drafts(refresh: bool = False) -> dict[str, dict]:
    docs: dict[str, dict] = {}

    def add(o: dict, topic: str, wg: str | None) -> None:
        if o["name"] in docs:
            if topic not in docs[o["name"]]["topics"]:
                docs[o["name"]]["topics"].append(topic)
            return
        docs[o["name"]] = {
            "doc_id": o["name"],
            "doc_type": "draft",
            "title": o["title"],
            "status": "INTERNET-DRAFT",
            "stream": "IETF" if wg else "individual",  # individual = a proposal no working group has adopted
            "wg": wg,
            "area": None,
            "pub_date": o["time"][:10],
            "abstract": o["abstract"].strip(),
            "obsoletes": [],
            "obsoleted_by": [],
            "updates": [],
            "updated_by": [],
            "in_scope": True,
            "topics": [topic],
            "draft_rev": o["rev"],
            "draft_state": "active",
            "expires": (o["expires"] or "")[:10] or None,
            "url": f"{DT}/doc/{o['name']}/",
        }

    for topic, cfg in TOPICS.items():
        for wg in cfg["draft_wgs"]:
            for o in _active_drafts(f"group__acronym={wg}", wg, refresh):
                add(o, topic, wg)
        for term in cfg["draft_terms"]:
            for o in _active_drafts(f"name__contains={term}", f"name-{term}", refresh):
                if wants_draft(o["name"], cfg["draft_tokens"]):
                    parts = o["name"].split("-")
                    add(o, topic, parts[2] if parts[1] in ("ietf", "irtf") else None)
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
