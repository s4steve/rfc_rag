"""Golden questions -> recall@5. Run inside the stack: docker compose run --rm ingest python -m tests.eval"""

from rfc_rag.server import search

# (question, acceptable docs, section prefix or None)
GOLDEN = [
    ("What fields are in the DNS message header?", {"RFC1035"}, "4.1.1"),
    ("How does domain name compression work in DNS messages?", {"RFC1035"}, "4.1.4"),
    ("RRSIG RDATA wire format", {"RFC4034"}, "3.1"),
    ("How does NSEC3 opt-out work?", {"RFC5155"}, None),
    ("Format of the EDNS0 OPT pseudo-RR", {"RFC6891"}, "6.1"),
    ("How long should negative answers be cached?", {"RFC2308"}, None),
    ("Which port does DNS over TLS use?", {"RFC7858"}, None),
    ("DNS over HTTPS application/dns-message media type", {"RFC8484"}, None),
    ("What does the CD checking disabled bit mean for a validating resolver?", {"RFC4035", "RFC6840"}, None),
    ("Serving stale data when authoritative servers are unreachable", {"RFC8767"}, None),
    ("Extended DNS error codes in responses", {"RFC8914"}, None),
    ("DNS cookies to protect against off-path spoofing", {"RFC7873", "RFC9018"}, None),
    ("QNAME minimisation to improve privacy", {"RFC9156"}, None),
    ("What is a lame delegation?", {"RFC9499"}, None),
    ("SVCB AliasMode", {"RFC9460"}, "2.4.2"),
    ("Providing minimal-sized responses to qtype ANY", {"RFC8482"}, None),
    ("DNSSEC key rollover timing", {"RFC7583", "RFC6781"}, None),
    ("TSIG secret key transaction authentication", {"RFC8945"}, None),
    ("TCP transport requirements for DNS implementations", {"RFC7766"}, None),
    ("Clarifications to TTL and RRset semantics", {"RFC2181"}, None),
]


def main() -> None:
    doc_hits = sec_hits = sec_total = 0
    for q, docs, sec in GOLDEN:
        res = search(q, k=5)
        got = [(r["doc_id"], r["section_id"]) for r in res]
        d_ok = any(d in docs for d, _ in got)
        s_ok = sec and any(d in docs and (s or "").startswith(sec) for d, s in got)
        doc_hits += d_ok
        if sec:
            sec_total += 1
            sec_hits += bool(s_ok)
        mark = "ok " if d_ok else "MISS"
        print(f"{mark} {q[:60]:60} -> {', '.join(f'{d} §{s}' for d, s in got[:3])}")
    print(f"\ndoc recall@5: {doc_hits}/{len(GOLDEN)}   section recall@5: {sec_hits}/{sec_total}")


if __name__ == "__main__":
    main()
