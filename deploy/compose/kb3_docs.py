#!/usr/bin/env python3
"""The test documents of PATH P2 (KB3, the conversion of originals), made with the standard library only. Every text is invented; no business document is used.
Each kind takes the words the acceptance searches for (unique per run), so that a hit is the document of this run and nothing else.

  python3 -I deploy/compose/kb3_docs.py pdf3 OUT W1 W2 W3       a PDF of three pages: W1 on page 1, W2 on page 2, W3 on page 3
  python3 -I deploy/compose/kb3_docs.py docx OUT WTITLE WSUB WCELL   headings "Travel handbook WTITLE" > "Lodging WSUB", a table whose second row has WCELL
  python3 -I deploy/compose/kb3_docs.py pptx OUT W1 W2 W3       three slides, one word on each
  python3 -I deploy/compose/kb3_docs.py xlsx OUT WROW           a sheet "Totals": WROW is in the cell B7
  python3 -I deploy/compose/kb3_docs.py bigxlsx OUT N WKEEP WDROP    a sheet "Big" of N rows: WKEEP in A3, WDROP in A<N>, narrow rows
  python3 -I deploy/compose/kb3_docs.py scan OUT                 a PDF of one page that is only a picture (no text layer)
"""
import sys
import zipfile
from xml.sax.saxutils import escape

CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/></Types>')
RELS = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>'
OD = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument'
WNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def package(path, parts):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CT)
        for name, data in parts.items():
            z.writestr(name, data)


def pdf(path, pages, picture=False):
    """pages: a list of lists of lines of text (Helvetica); picture: one page that is an 8x8 grey image only"""
    objs = {}
    n_pages = len(pages)
    kids = ' '.join(f'{4 + 2 * i} 0 R' for i in range(n_pages))
    objs[1] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objs[2] = f'<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>'.encode()
    objs[3] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'
    for i, lines in enumerate(pages):
        page, content = 4 + 2 * i, 5 + 2 * i
        if picture:
            stream = b'q 400 0 0 400 100 200 cm /Im0 Do Q'
            res = '<< /XObject << /Im0 %d 0 R >> >>' % (4 + 2 * n_pages)
        else:
            body = ['BT /F1 12 Tf 72 720 Td 16 TL']
            for line in lines:
                body.append('(%s) Tj T*' % line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)'))
            body.append('ET')
            stream = '\n'.join(body).encode('latin-1')
            res = '<< /Font << /F1 3 0 R >> >>'
        objs[page] = f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources {res} /Contents {content} 0 R >>'.encode()
        objs[content] = b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream'
    if picture:
        img = bytes((x * 4 + y * 4) % 256 for y in range(8) for x in range(8))
        objs[4 + 2 * n_pages] = b'<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 64 >>\nstream\n' + img + b'\nendstream'
    out = bytearray(b'%PDF-1.4\n')
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b'%d 0 obj\n' % num + objs[num] + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    for num in sorted(objs):
        out += b'%010d 00000 n \n' % offsets[num]
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, xref)
    open(path, 'wb').write(bytes(out))


def p(text, style=None):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ''
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def docx(path, wtitle, wsub, wcell):
    styles = f'<w:styles {WNS}>' + ''.join(f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/></w:style>' for sid, name in (('Heading1', 'heading 1'), ('Heading2', 'heading 2'), ('Normal', 'Normal'))) + '</w:styles>'
    rows = [['Item', 'Limit per night', 'Receipt'], ['Hotel', f'600 {wcell}', 'required'], ['Meals', '100', 'not required']]
    tbl = '<w:tbl><w:tblPr/>' + ''.join('<w:tr>' + ''.join(f'<w:tc>{p(c)}</w:tc>' for c in r) + '</w:tr>' for r in rows) + '</w:tbl>'
    body = ''.join([p(f'Travel handbook {wtitle}', 'Heading1'), p('This handbook is an invented test document.'), p(f'Lodging {wsub}', 'Heading2'),
                    p('The limits for a night in a hotel are in the table.'), tbl, p('Meals', 'Heading2'), p('Meals are paid by the day.')])
    package(path, {'_rels/.rels': RELS % f'<Relationship Id="r1" Type="{OD}" Target="word/document.xml"/>',
                   'word/document.xml': f'<w:document {WNS}><w:body>{body}</w:body></w:document>', 'word/styles.xml': styles})


def pptx(path, w1, w2, w3):
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

    def sp(text, ph):
        paras = ''.join(f'<a:p><a:r><a:t>{escape(t)}</a:t></a:r></a:p>' for t in text.split('\n'))
        return f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="x"/><p:cNvSpPr/><p:nvPr><p:ph type="{ph}"/></p:nvPr></p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>'

    def slide(title, body):
        return f'<p:sld {ns}><p:cSld><p:spTree>{sp(title, "title")}{sp(body, "body")}</p:spTree></p:cSld></p:sld>'

    parts = {'_rels/.rels': RELS % f'<Relationship Id="r1" Type="{OD}" Target="ppt/presentation.xml"/>',
             'ppt/presentation.xml': f'<p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/><p:sldId id="258" r:id="rId3"/></p:sldIdLst></p:presentation>',
             'ppt/_rels/presentation.xml.rels': RELS % ''.join(f'<Relationship Id="rId{i}" Type="x/slide" Target="slides/slide{i}.xml"/>' for i in (1, 2, 3))}
    for i, (title, body, w) in enumerate((('Depot review', 'Parcels were sorted on time.', w1), ('Staffing plan', 'Two shifts cover the dock.', w2), ('Next steps', 'Review the rota in spring.', w3)), 1):
        parts[f'ppt/slides/slide{i}.xml'] = slide(title, f'{body}\nMarker {w}')
    package(path, parts)


def colname(n):
    s = ''
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx(path, sheet, rows):
    """rows: an iterator of (row number, [values]); strings are inline"""
    wb = ('<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          f'<sheets><sheet name="{escape(sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CT)
        z.writestr('_rels/.rels', RELS % f'<Relationship Id="r1" Type="{OD}" Target="xl/workbook.xml"/>')
        z.writestr('xl/workbook.xml', wb)
        z.writestr('xl/_rels/workbook.xml.rels', RELS % '<Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/>')
        with z.open('xl/worksheets/sheet1.xml', 'w', force_zip64=True) as f:
            f.write(b'<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>')
            for number, values in rows:
                cells = ''.join(f'<c r="{colname(c)}{number}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' if isinstance(v, str) else f'<c r="{colname(c)}{number}"><v>{v}</v></c>'
                                for c, v in enumerate(values, 1))
                f.write(f'<row r="{number}">{cells}</row>'.encode())
            f.write(b'</sheetData></worksheet>')


def main():
    kind, out, *w = sys.argv[1:]
    if kind == 'pdf3':
        pdf(out, [[f'Page {i}. Dock rota for week {i}.', f'Marker {w[i - 1]} is on this page.', 'The text is invented for the acceptance test.'] for i in (1, 2, 3)])
    elif kind == 'scan':
        pdf(out, [[]], picture=True)
    elif kind == 'docx':
        docx(out, *w)
    elif kind == 'pptx':
        pptx(out, *w)
    elif kind == 'xlsx':
        rows = [(1, ['Region', 'Note', 'Parcels'])] + [(i, [f'North{i}', f'{w[0]} check' if i == 7 else f'regular line {i}', i * 11]) for i in range(2, 10)]
        xlsx(out, 'Totals', iter(rows))
    elif kind == 'bigxlsx':
        n = int(w[0])
        # as narrow as a row can be (a marker or `x`, and the row number): every chunk the sheet makes is an embedding the platform's CPUs must do (about 100 for 10,000 rows)
        xlsx(out, 'Big', ((i, [w[1] if i == 3 else (w[2] if i == n else 'x'), i]) for i in range(1, n + 1)))
    else:
        raise SystemExit('unknown kind ' + kind)


main()
