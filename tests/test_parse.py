from rfc_rag.fetch import draft_relations, errata_section
from rfc_rag.parse import citations, parse_txt, parse_xml, section_text

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
