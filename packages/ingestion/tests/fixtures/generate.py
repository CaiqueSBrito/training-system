"""Gerador determinístico, somente biblioteca padrão: python .../generate.py."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).parent
(ROOT / "study.md").write_text("# Biology\n\nCells contain genetic material.\n\n## Details\n\nMembranes protect cells.\n\n| Part | Function |\n| --- | --- |\n| Nucleus | DNA |\n\n# Chemistry\n\nAtoms form molecules.\n", encoding="utf-8")
files = {
    "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
    "_rels/.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
    "word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Biology</w:t></w:r></w:p><w:p><w:r><w:t>Cells contain genetic material.</w:t></w:r></w:p><w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Details</w:t></w:r></w:p><w:tbl><w:tblPr/><w:tblGrid><w:gridCol w:w="3000"/><w:gridCol w:w="3000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>Part</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Function</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>Nucleus</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>DNA</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chemistry</w:t></w:r></w:p><w:p><w:r><w:t>Atoms form molecules.</w:t></w:r></w:p></w:body></w:document>',
}
files["[Content_Types].xml"] = files["[Content_Types].xml"].replace('</Types>', '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
files["word/_rels/document.xml.rels"] = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'
files["word/styles.xml"] = '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="48"/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style></w:styles>'
with ZipFile(ROOT / "study.docx", "w", compression=ZIP_DEFLATED) as archive:
    for name, content in files.items():
        archive.writestr(ZipInfo(name, (2020, 1, 1, 0, 0, 0)), content)
# PDF vetorial com duas páginas, títulos grandes e tabela com grade.
streams = []
for title, paragraph in [("Biology", "Cells contain genetic material."), ("Chemistry", "Atoms form molecules.")]:
    commands = f'BT /F1 24 Tf 60 750 Td ({title}) Tj ET\nBT /F1 12 Tf 60 700 Td ({paragraph}) Tj ET\n'
    commands += 'BT /F1 16 Tf 60 660 Td (Details) Tj ET\n'
    for y in (620, 590, 560):
        commands += f'60 {y} m 420 {y} l S\n'
    for x in (60, 240, 420):
        commands += f'{x} 560 m {x} 620 l S\n'
    for x, y, value in [(70, 600, "Part"), (250, 600, "Function"), (70, 570, "Nucleus"), (250, 570, "DNA")]:
        commands += f'BT /F1 12 Tf {x} {y} Td ({value}) Tj ET\n'
    streams.append(commands.encode())
objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>']
for index, data in enumerate(streams):
    objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents {4+2*index} 0 R >>'.encode(), b'<< /Length ' + str(len(data)).encode() + b' >>\nstream\n' + data + b'endstream'])
objects.append(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>')
output = b'%PDF-1.4\n'
offsets = [0]
for index, data in enumerate(objects, 1):
    offsets.append(len(output))
    output += f'{index} 0 obj\n'.encode() + data + b'\nendobj\n'
xref = len(output)
output += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
output += b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])
output += f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
(ROOT / "study.pdf").write_bytes(output)
