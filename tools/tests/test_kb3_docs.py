"""Tests of deploy/compose/kb3_docs.py (the test documents of PATH P2) and deploy/compose/p2_probe.py (its forged ticket): the documents must be what the acceptance says they are
(a PDF of three pages with a word on each, a DOCX with a heading path and a table, a PPTX of three slides, an XLSX of the stated rows, a PDF that has no text operators), or PATH P2 would
prove nothing about the converters; the forged ticket must have the shape the CMI's verifier takes before it looks at the signature. Standard library only."""
import base64
import json
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "deploy" / "compose"


def make(kind, out, *words):
    subprocess.run([sys.executable, "-I", str(COMPOSE / "kb3_docs.py"), kind, str(out), *words], check=True)


class Documents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pdf_has_three_pages_each_with_its_word(self):
        make("pdf3", self.dir / "a.pdf", "wone", "wtwo", "wthree")
        raw = (self.dir / "a.pdf").read_bytes()
        self.assertTrue(raw.startswith(b"%PDF-1.4") and raw.rstrip().endswith(b"%%EOF"))
        self.assertEqual(len(re.findall(rb"/Type /Page ", raw)), 3)
        for i, w in enumerate((b"wone", b"wtwo", b"wthree"), 1):
            self.assertIn(b"Marker " + w + b" is on this page", raw)
            self.assertIn(b"Page %d." % i, raw)
        xref = int(re.search(rb"startxref\n(\d+)", raw).group(1))   # the cross-reference table is where the trailer says, and every offset points at an object
        self.assertTrue(raw[xref:].startswith(b"xref"))
        offsets = [int(m) for m in re.findall(rb"(\d{10}) 00000 n ", raw)]
        self.assertTrue(offsets and all(re.match(rb"\d+ 0 obj", raw[o:]) for o in offsets))

    def test_scan_has_an_image_and_no_text_operator(self):
        make("scan", self.dir / "s.pdf")
        raw = (self.dir / "s.pdf").read_bytes()
        self.assertIn(b"/Subtype /Image", raw)
        self.assertNotIn(b"BT", raw)
        self.assertNotIn(b" Tj", raw)

    def test_docx_has_the_heading_path_and_a_table_with_the_cell_word(self):
        make("docx", self.dir / "a.docx", "wtitle", "wsub", "wcell")
        with zipfile.ZipFile(self.dir / "a.docx") as z:
            body = z.read("word/document.xml").decode()
            styles = z.read("word/styles.xml").decode()
        self.assertIn("Travel handbook wtitle", body)
        self.assertIn("Lodging wsub", body)
        self.assertIn("<w:tbl>", body)
        self.assertRegex(body, r"<w:tc>.*600 wcell.*</w:tc>")
        self.assertIn('w:val="heading 1"', styles)

    def test_pptx_has_three_slides_in_order(self):
        make("pptx", self.dir / "a.pptx", "w1", "w2", "w3")
        with zipfile.ZipFile(self.dir / "a.pptx") as z:
            self.assertEqual(sorted(n for n in z.namelist() if n.startswith("ppt/slides/slide")), [f"ppt/slides/slide{i}.xml" for i in (1, 2, 3)])
            for i, w in enumerate(("w1", "w2", "w3"), 1):
                self.assertIn(f"Marker {w}", z.read(f"ppt/slides/slide{i}.xml").decode())

    def test_xlsx_puts_the_word_in_b7(self):
        make("xlsx", self.dir / "a.xlsx", "wrow")
        with zipfile.ZipFile(self.dir / "a.xlsx") as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('name="Totals"', z.read("xl/workbook.xml").decode())
        self.assertRegex(sheet, r'<c r="B7" t="inlineStr"><is><t>wrow check</t>')
        self.assertNotIn("wrow", sheet.replace("wrow check", ""))

    def test_bigxlsx_keeps_the_word_in_row_3_and_ends_with_the_other_in_the_last_row(self):
        make("bigxlsx", self.dir / "b.xlsx", "10500", "wkeep", "wdrop")
        with zipfile.ZipFile(self.dir / "b.xlsx") as z:
            sheet = z.read("xl/worksheets/sheet1.xml").decode()
        self.assertEqual(sheet.count("<row "), 10500)
        self.assertRegex(sheet, r'<c r="A3" t="inlineStr"><is><t>wkeep</t>')
        self.assertRegex(sheet, r'<row r="10500">.*wdrop')


class ForgedTicket(unittest.TestCase):
    def test_the_forged_ticket_has_three_parts_a_hs256_header_and_the_claims_of_a_write_ticket(self):
        # run the probe against a local listener that answers 403 and keeps the request's headers
        import http.server
        import threading
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                seen["auth"] = self.headers.get("Authorization")
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(403)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            out = subprocess.run([sys.executable, "-I", "-", "forged", f"http://127.0.0.1:{srv.server_port}", "write"], input=(COMPOSE / "p2_probe.py").read_text(), text=True, capture_output=True, check=True)
        finally:
            srv.shutdown()
        self.assertEqual(out.stdout.strip(), "403")
        parts = seen["auth"].removeprefix("Bearer ").split(".")
        self.assertEqual(len(parts), 3)
        dec = lambda s: json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))
        self.assertEqual(dec(parts[0]), {"alg": "HS256", "typ": "JWT"})
        claims = dec(parts[1])
        self.assertEqual(claims["kind"], "write")
        self.assertTrue(claims["jti"].startswith("forged-") and claims["exp"] > claims["iat"])


if __name__ == "__main__":
    unittest.main()
