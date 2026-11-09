from rfc_rag.fetch import TOPICS, draft_relations, errata_section, mark_scope, wants_draft
from rfc_rag.parse import citations, parse_txt, parse_xml, section_text


def test_email_scope():
    def doc(title, wg=None, status="PROPOSED STANDARD", **edges):
        return {"title": title, "wg": wg, "status": status, "in_scope": False, "topics": [], "obsoletes": [],
                "obsoleted_by": [], "updates": [], "updated_by": [], **edges}
    docs = {
        "RFC6152": doc("SMTP Service Extension for 8-bit MIME Transport", updates=["RFC3261"]),
        "RFC8461": doc("SMTP MTA Strict Transport Security (MTA-STS)", obsoletes=["RFC9000"]),
        "RFC9000": doc("Old transport"),
        "RFC3261": doc("SIP: Session Initiation Protocol"),  # only an updates edge: not followed for email
        "RFC8405": doc("Shortest Path First (SPF) Back-Off Delay Algorithm"),
        "RFC196": doc("Mail Box Protocol", status="UNKNOWN"),
        "RFC3853": doc("S/MIME AES Requirement for SIP", wg="sip"),
        "RFC2912": doc("Registration of the text/foo MIME Sub-Type"),
        "RFC7672": doc("SMTP Security via Opportunistic DNS-Based Authentication"),  # both topics
    }
    mark_scope(docs)
    assert {d for d, m in docs.items() if "email" in m["topics"]} == {"RFC6152", "RFC8461", "RFC9000", "RFC7672"}
    assert docs["RFC7672"]["topics"] == ["dns", "email"] and docs["RFC3261"]["in_scope"] is False


def test_wants_draft_matches_whole_tokens():
    tokens = TOPICS["email"]["draft_tokens"]
    assert wants_draft("draft-ietf-emailcore-rfc5322bis", tokens) and wants_draft("draft-xie-dkim-sm2-sm3", tokens)
    assert not wants_draft("draft-ietf-nmop-simap-concept", tokens)
    assert not wants_draft("draft-baismail-glcn-sdlp", tokens)

# Excerpt shaped like RFC 1035: body at column 0, page break mid-paragraph, no TOC.
RFC1035 = """\
Network Working Group                                     P. Mockapetris
Request for Comments: 1035                                           ISI
                                                           November 1987

1. STATUS OF THIS MEMO

This RFC describes the details of the domain system and protocol.

2. INTRODUCTION

2.1. Overview

The goal of domain names is to provide a mechanism for naming resources.

4. MESSAGES

4.1. Format

All communications inside of the domain protocol are carried in a single
format called a message.

4.1.1. Header section format

The header contains the following fields:

                                    1  1  1  1  1  1
      0  1  2  3  4  5  6  7  8  9  0  1  2  3  4  5
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
    |                      ID                       |
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+
    |QR|   Opcode  |AA|TC|RD|RA|   Z    |   RCODE   |
    +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+

AA              Authoritative Answer - this bit is valid in responses.
                Note that the contents of the answer section may have
                multiple owner names because of aliases.  The AA bit



Mockapetris                                                    [Page 26]
\f
RFC 1035        Domain Implementation and Specification    November 1987


                corresponds to the name which matches the query name.

1. a list item that is not a heading

5. REFERENCES and BIBLIOGRAPHY

[Dyer 87]       S. Dyer, F. Hsu, "Hesiod", Project Athena.
"""


def test_txt_sections_and_page_break():
    secs = {s["id"]: s for s in parse_txt(RFC1035)}
    assert list(secs) == ["2", "2.1", "4", "4.1", "4.1.1", "5"]  # status memo dropped, list item ignored
    assert secs["4.1.1"]["parent"] == "4.1" and secs["4.1.1"]["depth"] == 3
    text = section_text(secs["4.1.1"])
    assert "[Page" not in text and "Domain Implementation and Specification" not in text
    assert "The AA bit corresponds to the name" in text  # rejoined across the page break


def test_txt_figure_is_one_block():
    figs = [b for b in parse_txt(RFC1035)[4]["blocks"] if b["kind"] == "figure"]
    assert len(figs) == 1
    assert sum(ln.startswith("+--+") for ln in figs[0]["text"].splitlines()) == 3 and "RCODE" in figs[0]["text"]


def test_txt_references_not_embedded():
    secs = {s["id"]: s for s in parse_txt(RFC1035)}
    assert secs["5"]["embed"] is False and secs["4.1.1"]["embed"] is True


RFC9460 = """<rfc number="9460">
<front><abstract pn="section-abstract"><t>This document specifies the SVCB and HTTPS RR types.</t></abstract></front>
<middle>
<section anchor="status" numbered="false" pn="section-boilerplate.1"><name>Status of This Memo</name><t>x</t></section>
<section anchor="svcb" numbered="true" pn="section-2"><name>The SVCB Record Type</name>
  <t>The SVCB DNS RR type.</t>
  <section anchor="rdata" numbered="true" pn="section-2.2"><name>RDATA Wire Format</name>
    <t>Labels per <xref section="3.1" sectionFormat="of" target="RFC1035" derivedContent="RFC1035"/>.</t>
    <ul><li>a 2-octet field for SvcPriority</li><li>the TargetName</li></ul>
    <figure><name>Wire format</name><artwork type="ascii-art">
    +--+--+
    |  ID |
    +--+--+
</artwork></figure>
  </section>
</section>
</middle>
<back>
<references pn="section-15"><name>References</name>
  <references pn="section-15.1"><name>Normative References</name>
    <reference anchor="RFC1035"><front><title>Domain names</title></front></reference>
  </references>
  <references pn="section-15.2"><name>Informative References</name>
    <reference anchor="RFC1035"><front><title>Domain names</title></front></reference>
    <reference anchor="RFC7858"><front><title>DNS over TLS</title></front></reference>
    <reference anchor="I-D.ietf-dnsop-svcb-dane"><front><title>Using DANE with SVCB</title></front></reference>
    <reference anchor="RFC9460"><front><title>Self</title></front></reference>
  </references>
</references>
<section anchor="decoding" numbered="true" pn="section-appendix.a"><name>Decoding Text</name><t>Escapes.</t></section>
</back>
</rfc>"""


def test_xml_sections():
    secs = {s["id"]: s for s in parse_xml(RFC9460)}
    assert list(secs) == ["abstract", "2", "2.2", "15", "15.1", "15.2", "A"]
    assert secs["2.2"]["parent"] == "2"
    kinds = [b["kind"] for b in secs["2.2"]["blocks"]]
    assert kinds == ["text", "text", "figure"]
    assert "Section 3.1 of RFC1035" in secs["2.2"]["blocks"][0]["text"]
    assert secs["2.2"]["blocks"][2]["text"].startswith("Figure: Wire format\n+--+--+")
    assert not secs["15.1"]["embed"] and secs["A"]["embed"]


def test_errata_section_and_draft_relations():
    assert errata_section("4.1.1") == "4.1.1"
    assert errata_section("Section 4.1.1.") == "4.1.1"
    assert errata_section("Appendix A.2") == "A.2"
    assert errata_section("GLOBAL") is None
    head = "DNSOP                                    A. Author\nInternet-Draft\nUpdates: 1035, 2181 (if approved)          Example\n"
    assert draft_relations(head) == {"updates": ["RFC1035", "RFC2181"], "obsoletes": []}


def test_citations():
    assert citations(parse_xml(RFC9460), "RFC9460") == [
        ("RFC1035", "normative"), ("RFC7858", "informative"), ("draft-ietf-dnsop-svcb-dane", "informative")]
    # text refs: "RFC 1035"-style mentions, kind unknown without a normative/informative title
    secs = [{"id": "9", "title": "REFERENCES", "parent": None, "embed": False,
             "blocks": [{"kind": "text", "text": "[RFC1035] Mockapetris, RFC 1035, 1987. draft-ietf-dnsop-foo-03.txt"}]}]
    assert citations(secs, "RFC2181") == [("RFC1035", "unknown"), ("draft-ietf-dnsop-foo", "unknown")]


def test_refs_titles():
    from rfc_rag.parse import _is_refs
    for t in ("References", "REFERENCES and BIBLIOGRAPHY", "Normative References", "- References", "Informational References"):
        assert _is_refs(t), t
    for t in ("Domain Preferences", "Key Server Preferences", "Looking up Blob References", "In-Reply-To and References Fields"):
        assert not _is_refs(t), t
    assert not _is_refs("References", depth=3)  # the header field, e.g. section 3.6.4.1


def _body(*paras):
    return "1. Intro\n\nSome text here.\n\n2. Syntax\n\n" + "\n\n".join(paras) + "\n"


def test_preformatted_keeps_line_breaks():
    abnf = '   addr-spec  =  local-part "@" domain\n   domain     =  dot-atom /\n                 domain-literal'
    talk = "   C: a001 LOGIN alice secret\n   S: a001 OK done"
    hdrs = "   From: a@example.com\n   To: b@example.com\n   Subject: hello"
    zone = "   example.com. 300 IN MX 10 mail\n   mail 300 IN A 192.0.2.1"
    prose = "   Note: this sentence wraps on to a second\n   line and is ordinary prose."
    blocks = parse_txt(_body(abnf, prose, talk, hdrs, zone))[1]["blocks"]
    assert [b["kind"] for b in blocks] == ["figure", "text", "figure"]
    assert "@\" domain\ndomain" in blocks[0]["text"] and "second line" in blocks[1]["text"]
    assert blocks[2]["text"].count("\n") >= 6 and "C: a001 LOGIN alice secret\nS: a001" in blocks[2]["text"]


def test_indented_subheadings_and_bare_appendix():
    text = """1. INTRODUCTION

   Text of the introduction runs on for a while.

     1.1.  Scope

   Text of the scope section.

     1. a list item that is not a heading.

2. SYNTAX

   Text of the syntax section.

A. EXAMPLES

   An example.
"""
    secs = parse_txt(text)
    assert [s["id"] for s in secs] == ["1", "1.1", "2", "A"]
    assert secs[1]["parent"] == "1"


def test_xml_nested_code_is_a_figure():
    x = """<rfc><middle><section pn="section-1"><name>Ex</name><ul><li><t>Use:</t><sourcecode>a
  b</sourcecode></li></ul></section></middle></rfc>"""
    assert parse_xml(x)[0]["blocks"] == [{"kind": "text", "text": "- Use:"}, {"kind": "figure", "text": "a\n  b"}]


def test_citations_hyphen_form():
    secs = [{"id": "9", "title": "REFERENCES", "parent": None, "embed": False,
             "blocks": [{"kind": "text", "text": "Crocker, D., RFC-822, 1982"}]}]
    assert citations(secs, "RFC1123") == [("RFC822", "unknown")]


def test_abnf_after_page_break_starts_a_new_block():
    page = "\n\n\nResnick                 Standards Track                    [Page 17]\n\f\nRFC 5322          Internet Message Format        October 2008\n\n\n"
    secs = parse_txt("1. Intro\n\nIn which they are used." + page + '   addr-spec  =  local-part "@" domain\n')
    assert [b["kind"] for b in secs[0]["blocks"]] == ["text", "figure"]
