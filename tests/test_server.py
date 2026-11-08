import asyncio
import xml.etree.ElementTree as ET

from rfc_rag.fetch import add_subseries
from rfc_rag.server import norm_id, require_bearer


def test_norm_id():
    assert norm_id("1035") == norm_id("rfc 1035") == norm_id("RFC1035") == "RFC1035"
    assert norm_id("STD 13") == norm_id("std0013") == "STD13"
    assert norm_id("BCP91") == "BCP91"
    assert norm_id("draft-ietf-dnsop-foo-03") == "draft-ietf-dnsop-foo"


def test_subseries():
    root = ET.fromstring(
        '<rfc-index xmlns="https://www.rfc-editor.org/rfc-index">'
        "<std-entry><doc-id>STD13</doc-id><is-also><doc-id>RFC1034</doc-id><doc-id>RFC1035</doc-id></is-also></std-entry>"
        "<bcp-entry><doc-id>BCP91</doc-id><is-also><doc-id>RFC3901</doc-id></is-also></bcp-entry>"
        "<bcp-entry><doc-id>BCP1</doc-id></bcp-entry></rfc-index>")
    docs = {d: {"also": []} for d in ("RFC1034", "RFC1035", "RFC3901", "RFC2181")}
    add_subseries(root, docs)
    assert docs["RFC1035"]["also"] == ["STD13"] and docs["RFC3901"]["also"] == ["BCP91"]
    assert docs["RFC2181"]["also"] == []


def test_require_bearer():
    async def ok(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    def call(headers):
        sent = []

        async def send(m):
            sent.append(m)

        asyncio.run(require_bearer(ok, "s3cret")({"type": "http", "headers": headers}, None, send))
        return sent[0]["status"]

    assert call([]) == 401
    assert call([(b"authorization", b"Bearer wrong")]) == 401
    assert call([(b"authorization", b"Bearer s3cret")]) == 200
