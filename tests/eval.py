"""Golden questions -> recall@5 and MRR@10.

Run inside the stack:  docker compose run --rm ingest python -m tests.eval [--rerank off|on|both]
"""

import argparse
import statistics
import time

from rfc_rag.server import _search

# (question, acceptable docs, section prefix or None)
GOLDEN = [
    # --- section-specific
    ("What fields are in the DNS message header?", {"RFC1035"}, "4.1.1"),
    ("How does domain name compression work in DNS messages?", {"RFC1035"}, "4.1.4"),
    ("RRSIG RDATA wire format", {"RFC4034"}, "3.1"),
    ("Format of the EDNS0 OPT pseudo-RR", {"RFC6891"}, "6.1"),
    ("SVCB AliasMode", {"RFC9460"}, "2.4.2"),
    ("Wire format of the DNSKEY resource record", {"RFC4034"}, "2.1"),
    ("How is the key tag of a DNSKEY computed?", {"RFC4034"}, "B"),
    ("Canonical ordering of owner names for DNSSEC", {"RFC4034"}, "6.1"),
    ("How is the digest in a DS record calculated?", {"RFC4034"}, "5.1.4"),
    ("NSEC3 hash calculation with salt and iterations", {"RFC5155"}, "5"),
    ("NSEC3PARAM record wire format", {"RFC5155"}, "4.2"),
    ("Proving the closest encloser with NSEC3", {"RFC5155"}, "7.2.1"),
    ("What are the fields of the SOA record RDATA?", {"RFC1035"}, "3.3.13"),
    ("Syntax of master files (zone file format)", {"RFC1035"}, "5.1"),
    ("Step-by-step algorithm a resolver uses to answer a request", {"RFC1034"}, "5.3.3"),
    ("Algorithm a name server follows to process an incoming query", {"RFC1034"}, "4.3.2"),
    ("What to do when records in an RRset have different TTLs", {"RFC2181"}, "5.2"),
    ("Zone cuts and which data is authoritative", {"RFC2181", "RFC1034"}, None),
    ("How long negative answers may be cached, and the SOA TTL rule", {"RFC2308"}, "5"),
    ("How the TSIG MAC is computed over the message", {"RFC8945"}, "4.3"),
    ("Extended RCODE and the flags field inside the OPT record", {"RFC6891"}, "6.1.3"),
    ("Requestor's UDP payload size advertised in EDNS", {"RFC6891"}, "6.2.3"),
    ("DoH GET request with the dns query parameter", {"RFC8484"}, "4.1"),
    ("Two-octet length field prefix for DNS messages over TCP", {"RFC7766", "RFC1035"}, None),
    ("SVCB alpn SvcParamKey", {"RFC9460"}, "7.1"),
    ("Wire format of the Extended DNS Error option", {"RFC8914"}, "2"),
    ("Format of the DNS COOKIE EDNS option with client and server cookies", {"RFC7873"}, "4"),
    ("How a validating resolver authenticates denial of existence", {"RFC4035"}, "5.4"),
    ("Checking an RRSIG to authenticate an RRset", {"RFC4035"}, "5.3"),
    ("Prerequisite section of a dynamic UPDATE message", {"RFC2136"}, "2"),  # ponytail: 2136 subsections are indented, parser keeps them in §2
    ("Format of an AXFR response", {"RFC5936"}, "2.2"),
    ("Text representation of unknown RR types using \\#", {"RFC3597"}, "5"),
    # --- paraphrased
    ("My resolver got a truncated UDP answer. What should it do next?", {"RFC7766", "RFC1035", "RFC6891", "RFC1123"}, None),
    ("Why can't a CNAME share a name with other records?", {"RFC1034", "RFC2181"}, None),
    ("Keep answering from cache past expiry when the authoritative servers are down", {"RFC8767"}, None),
    ("Use cached NSEC records to answer queries for nonexistent names without asking upstream", {"RFC8198"}, None),
    ("Automatically roll trust anchors when a zone's key-signing key changes", {"RFC5011"}, None),
    ("Redirect an entire subtree of the namespace to a different domain", {"RFC6672"}, None),
    ("Pass part of the client's IP address to authoritative servers for location-based answers", {"RFC7871"}, None),
    ("If a name does not exist, nothing underneath it exists either", {"RFC8020"}, None),
    ("Running DNS queries over the QUIC transport", {"RFC9250"}, None),
    ("Which signing algorithms must DNSSEC validators implement?", {"RFC9904"}, None),
    ("Let a child zone signal its new DS records to the parent automatically", {"RFC7344", "RFC8078"}, None),
    ("Using Edwards-curve signatures in DNSSEC", {"RFC8080"}, None),
    ("Remember resolution failures so resolvers don't hammer broken servers", {"RFC9520"}, None),
    ("What is glue?", {"RFC9499"}, None),
    ("Tell secondary servers immediately that a zone has changed", {"RFC1996"}, None),
    ("Add or delete records in a zone over the network", {"RFC2136"}, None),
    ("Transfer only the changes to a zone, not the whole zone", {"RFC1995"}, None),
    ("Pad encrypted DNS messages so their size leaks less", {"RFC7830", "RFC8467"}, None),
    ("How a client discovers that its network's resolver supports encrypted DNS", {"RFC9462", "RFC9463"}, None),
    ("Browse for services on a network using PTR, SRV and TXT records", {"RFC6763"}, None),
    ("Name resolution on the local link without a DNS server", {"RFC6762"}, None),
    ("Locate the host and port for a service such as LDAP via DNS", {"RFC2782"}, None),
    ("Recommended key sizes and lifetimes for operating a signed zone", {"RFC6781"}, None),
    ("How many NSEC3 iterations should a zone use?", {"RFC9276"}, None),
    ("Publish TLS certificate associations in DNS", {"RFC6698", "RFC7671"}, None),
    ("Restrict which certificate authorities may issue for a domain", {"RFC8659"}, None),
    ("Domain names reserved for testing and documentation", {"RFC2606", "RFC6761"}, None),
    ("Why .onion names must never be sent to the DNS", {"RFC7686"}, None),
    ("Zones every resolver should serve locally, like reverse lookups for private addresses", {"RFC6303"}, None),
    # --- exact terms
    ("NSEC3PARAM", {"RFC5155"}, None),
    ("EDE code 18 Prohibited", {"RFC8914"}, None),
    ("BADCOOKIE", {"RFC7873"}, None),
    ("SOA MINIMUM field", {"RFC2308", "RFC1035"}, None),
    ("TC bit", {"RFC1035", "RFC7766", "RFC2181"}, None),
    ("AD bit", {"RFC4035", "RFC6840"}, None),
    ("DO bit", {"RFC3225", "RFC4035", "RFC6891"}, None),
    ("ANCOUNT", {"RFC1035"}, None),
    ("ZONEMD", {"RFC8976"}, None),
    ("CSYNC", {"RFC7477"}, None),
    ("edns-key-tag option", {"RFC8145"}, None),
    ("IQUERY", {"RFC3425", "RFC1035"}, None),
    ("edns-tcp-keepalive", {"RFC7828"}, None),
    ("CHAIN query requests", {"RFC7901"}, None),
    ("SOURCE PREFIX-LENGTH SCOPE PREFIX-LENGTH", {"RFC7871"}, None),
    # --- status-aware: old terminology, the current doc must answer
    ("NXT record for authenticated denial of existence", {"RFC4034", "RFC4035", "RFC7129"}, None),
    ("KEY record holding a zone's public key for DNSSEC", {"RFC4034", "RFC4035"}, None),
    ("TSIG specification", {"RFC8945"}, None),
    ("EDNS0 extension mechanism", {"RFC6891"}, None),
    ("DNSSEC protocol modifications for resolvers and servers", {"RFC4035"}, None),
    ("DNS terminology definitions", {"RFC9499"}, None),
    ("DNS privacy considerations", {"RFC9076"}, None),
    ("Which port does DNS over TLS use?", {"RFC7858"}, None),
    ("DNS over HTTPS application/dns-message media type", {"RFC8484"}, None),
    ("What does the CD checking disabled bit mean for a validating resolver?", {"RFC4035", "RFC6840"}, None),
    # --- kept from the original set
    ("How does NSEC3 opt-out work?", {"RFC5155"}, None),
    ("How long should negative answers be cached?", {"RFC2308"}, None),
    ("Extended DNS error codes in responses", {"RFC8914"}, None),
    ("DNS cookies to protect against off-path spoofing", {"RFC7873", "RFC9018"}, None),
    ("QNAME minimisation to improve privacy", {"RFC9156"}, None),
    ("What is a lame delegation?", {"RFC9499"}, None),
    ("Providing minimal-sized responses to qtype ANY", {"RFC8482"}, None),
    ("DNSSEC key rollover timing", {"RFC7583", "RFC6781"}, None),
    ("TCP transport requirements for DNS implementations", {"RFC7766"}, None),
    ("Clarifications to TTL and RRset semantics", {"RFC2181"}, None),
]

GOLDEN_EMAIL = [
    # --- section-specific
    ("What is the syntax of an addr-spec email address?", {"RFC5322"}, "3.4.1"),
    ("How are long header fields folded?", {"RFC5322"}, "2.2.3"),
    ("Maximum line length of an email message", {"RFC5322"}, "2.1.1"),
    ("Which header fields are required and how many times can each appear?", {"RFC5322"}, "3.6"),
    ("Sequence of commands in an SMTP mail transaction", {"RFC5321"}, "3.3"),
    ("Size limits for SMTP local-part, domain and command line", {"RFC5321"}, "4.5.3.1"),
    ("How does an SMTP client use MX records to choose a destination host?", {"RFC5321"}, "5.1"),
    ("How long should an SMTP client retry delivery before giving up?", {"RFC5321"}, "4.5.4"),
    ("Handling of messages with a null reverse-path", {"RFC5321"}, "4.5.5"),
    ("Trace information added to a message by SMTP servers", {"RFC5321"}, "4.4"),
    ("Quoted-printable encoding rules", {"RFC2045"}, "6.7"),
    ("How is the boundary delimiter in a multipart body defined?", {"RFC2046"}, "5.1.1"),
    ("Syntax of an encoded-word for non-ASCII text in headers", {"RFC2047"}, "2"),
    ("Tags in the DKIM-Signature header field", {"RFC6376"}, "3.5"),
    ("DKIM relaxed body canonicalization algorithm", {"RFC6376"}, "3.4.4"),
    ("Tags in the DKIM public key DNS record", {"RFC6376"}, "3.6.1"),
    ("How the SPF check_host function works", {"RFC7208"}, "4"),
    ("Limit on DNS lookups during SPF evaluation", {"RFC7208"}, "4.6.4"),
    ("The SPF include mechanism", {"RFC7208"}, "5.2"),
    ("Data items of the IMAP FETCH command", {"RFC9051"}, "6.4.5"),
    ("IMAP system flags such as Seen and Answered", {"RFC9051"}, "2.3.2"),
    ("What is UIDVALIDITY in IMAP?", {"RFC9051"}, "2.3.1.1"),
    ("The Sieve fileinto action", {"RFC5228"}, "4.1"),
    ("The JMAP session resource", {"RFC8620"}, "2"),
    ("Format of the MTA-STS policy file", {"RFC8461"}, "3.2"),
    ("Fields of the ARC-Message-Signature header", {"RFC8617"}, "4.1.2"),
    # --- paraphrased
    ("Keep vacation auto-responders from replying to mailing lists", {"RFC3834", "RFC5230"}, None),
    ("Let recipients unsubscribe with a single click from the mail client", {"RFC8058"}, None),
    ("Get reports about TLS failures when others deliver mail to my domain", {"RFC8460"}, None),
    ("Sender insists a message is only relayed over verified TLS on every hop", {"RFC8689"}, None),
    ("Declare that a domain never accepts mail", {"RFC7505"}, None),
    ("Connect to mail servers with implicit TLS instead of upgrading the connection", {"RFC8314"}, None),
    ("Email addresses containing non-ASCII characters", {"RFC6530", "RFC6531", "RFC6532"}, None),
    ("Tell the sender that the recipient has displayed a message", {"RFC8098"}, None),
    ("Mailing lists break signatures; keep the authentication results across intermediaries", {"RFC8617"}, None),
    ("Separate service for mail clients to submit outgoing messages on port 587", {"RFC6409"}, None),
    ("Atomically move a message to another mailbox on the server", {"RFC6851"}, None),
    ("Have the server notify a connected client as soon as new mail arrives", {"RFC2177", "RFC9051"}, None),
    ("Format of the machine-readable bounce report", {"RFC3464"}, None),
    ("Discover a user's mail servers from their email domain using DNS", {"RFC6186"}, None),
    # --- exact terms
    ("STARTTLS", {"RFC3207", "RFC8314", "RFC9051"}, None),
    ("Content-Transfer-Encoding", {"RFC2045"}, None),
    ("RFC5322.From alignment", {"RFC9989"}, None),
    ("Enhanced status code 5.7.1", {"RFC3463", "RFC5248", "RFC7372"}, None),
    ("8BITMIME", {"RFC6152"}, None),
    ("CONDSTORE", {"RFC7162"}, None),
    ("SMTPUTF8", {"RFC6531"}, None),
    ("Authentication-Results header field", {"RFC8601"}, None),
    ("List-Unsubscribe-Post", {"RFC8058"}, None),
    ("REQUIRETLS", {"RFC8689"}, None),
    ("PIPELINING", {"RFC2920"}, None),
    ("NOTIFY parameter of RCPT", {"RFC3461"}, None),
    # --- status-aware: the current doc must answer, not the one it replaced
    ("DMARC policy discovery and identifier alignment", {"RFC9989"}, None),
    ("DMARC aggregate feedback report format", {"RFC9990"}, None),
    ("DMARC failure reporting", {"RFC9991"}, None),
    ("IMAP protocol specification", {"RFC9051"}, None),
    ("SMTP protocol specification", {"RFC5321"}, None),
    ("Internet Message Format specification", {"RFC5322"}, None),
    ("SPF specification", {"RFC7208"}, None),
    ("S/MIME message specification", {"RFC8551"}, None),
    ("OpenPGP message format", {"RFC9580"}, None),
    # --- crossing into DNS
    ("DANE TLSA records for SMTP servers", {"RFC7672"}, None),
    ("Publish a user's OpenPGP key in DNS", {"RFC7929"}, None),
    ("Certificate authority restrictions for S/MIME certificates", {"RFC9495"}, None),
]

SETS = {"dns": GOLDEN, "email": GOLDEN_EMAIL}


def run(rerank: bool, golden: list, topic: str | None) -> dict:
    out, times = {}, []
    for q, docs, sec in golden:
        t = time.perf_counter()
        res = _search(q, k=10, rerank=rerank, topic=topic)
        times.append(time.perf_counter() - t)
        got = [(r["doc_id"], r["section_id"] or "") for r in res]
        rank = next((i + 1 for i, (d, _) in enumerate(got) if d in docs), None)
        sec_ok = bool(sec) and any(d in docs and (s == sec or s.startswith(sec + ".")) for d, s in got[:5])
        out[q] = {"doc5": rank is not None and rank <= 5, "rr": 1 / rank if rank else 0.0, "sec5": sec_ok, "top": got[:3]}
    out["_p50"] = statistics.median(times)
    return out


def summary(r: dict, golden: list) -> str:
    rows = [v for k, v in r.items() if not k.startswith("_")]
    n_sec = sum(1 for _, _, s in golden if s)
    return (f"doc recall@5 {sum(v['doc5'] for v in rows)}/{len(rows)}   "
            f"section recall@5 {sum(v['sec5'] for v in rows)}/{n_sec}   "
            f"doc MRR@10 {sum(v['rr'] for v in rows) / len(rows):.3f}   p50 {r['_p50'] * 1000:.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", choices=["off", "on", "both"], default="both")
    ap.add_argument("--topic", choices=list(SETS), default="dns", help="which question set to run")
    ap.add_argument("--scoped", action="store_true", help="also restrict search to --topic (default: search everything)")
    ap.add_argument("-v", action="store_true", help="print every question")
    a = ap.parse_args()
    golden = SETS[a.topic]
    modes = {"off": [False], "on": [True], "both": [False, True]}[a.rerank]
    res = {m: run(m, golden, a.topic if a.scoped else None) for m in modes}
    for m, r in res.items():
        if a.v or len(res) == 1:
            for q, docs, sec in golden:
                v = r[q]
                mark = "ok " if v["doc5"] else "MISS"
                print(f"{mark} {q[:60]:60} -> {', '.join(f'{d} §{s}' for d, s in v['top'])}")
    if len(res) == 2:
        print("changed (rank of first correct doc: off -> on):")
        for q, *_ in golden:
            a_, b_ = res[False][q], res[True][q]
            if (a_["rr"], a_["sec5"]) != (b_["rr"], b_["sec5"]):
                f = lambda v: f"{round(1 / v['rr']) if v['rr'] else '-'}{'+s' if v['sec5'] else ''}"
                print(f"  {f(a_):>4} -> {f(b_):<4} {q[:70]}")
    for m, r in res.items():
        print(f"rerank {'on ' if m else 'off'}: {summary(r, golden)}")


if __name__ == "__main__":
    main()
