"""T012 C03–C06/C08: offline, sem chaves, APIs ou bancos de aplicação."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from threading import Barrier, Lock
from zipfile import ZipFile

import pytest
from docling_core.types.doc import DoclingDocument, DocItemLabel, ProvenanceItem, BoundingBox
from training_contracts.ingestao import (
    CanonicalSpan, CanonicalTextRecord, CitationSpanSelection,
    resolve_citation_spans, validate_chunk_provenance, validate_canonical_spans,
)
from training_ingestion import ChunkConfig, Chunker, DoclingParser, ParsedDocument, IngestionError, count_tokens


def document(parts, version='v'):
    text = '\n\n'.join(p[0] for p in parts)
    canonical = CanonicalTextRecord(canonical_id='canonical-' + version, document_version_id=version,
        canonical_version='test-v1', parser_version='test', text=text,
        text_sha256=sha256(text.encode()).hexdigest(), created_at=datetime.now(timezone.utc))
    spans, offset = [], 0
    for i, (value, path, page, kind, h1) in enumerate(parts):
        if i:
            spans.append(CanonicalSpan(span_id=f's{i}-separator', canonical_id=canonical.canonical_id,
                start=offset, end=offset+2, page=None, section_path=(), h1_id=None,
                kind='separator', provenance='unknown'))
            offset += 2
        spans.append(CanonicalSpan(span_id=f's{i}', canonical_id=canonical.canonical_id,
            start=offset, end=offset+len(value), page=page, section_path=tuple(path), h1_id=h1,
            kind=kind, provenance='exact' if page else 'unknown'))
        offset += len(value)
    return ParsedDocument(canonical, tuple(spans), 'Study')


def assert_reconstruction(parsed, chunks, mappings):
    validate_canonical_spans(parsed.canonical, parsed.spans)
    cursor, recovered = 0, ''
    for chunk, mapping in zip(chunks, mappings, strict=True):
        validate_chunk_provenance(parsed.canonical, parsed.spans, chunk, mapping)
        assert chunk.start_offset <= cursor < chunk.end_offset
        assert [(r.start, r.end) for r in mapping.overlap_ranges] == (
            [(chunk.start_offset, cursor)] if chunk.start_offset < cursor else [])
        recovered += chunk.text[cursor-chunk.start_offset:]
        cursor = chunk.end_offset
    assert cursor == len(parsed.text)
    assert sha256(recovered.encode()).hexdigest() == parsed.canonical.text_sha256
    for left, right in zip(chunks, chunks[1:]):
        assert left.start_offset < right.start_offset and left.end_offset < right.end_offset


@pytest.mark.parametrize('value', ['https://example/'+'a'*7000, 'YWJj'*1700, '漢字'*3000,
    '😀'*3000, 'e\u0301'*3000, 'a '*7000])
@pytest.mark.parametrize('config', [ChunkConfig(40, 60), ChunkConfig(1, 2), ChunkConfig(70, 100)])
def test_C06_codepoints_lossless_and_declared_overlap(value, config):
    parsed = document([(value, ('A',), None, 'paragraph', 'a')])
    chunks, mappings = Chunker(config).split_with_provenance(parsed, 'v')
    assert len(chunks) > 1
    assert all(count_tokens(c.text) <= config.max_tokens for c in chunks)
    assert_reconstruction(parsed, chunks, mappings)
    assert chunks == Chunker(config).split(parsed, 'v')
    assert config.version != ChunkConfig(40, 61).version


def test_C05_paths_h1_occurrences_table_and_separators():
    parsed = document([('A', ('A',), 2, 'heading', 'a'),
        ('short one', ('A','H2'), 2, 'paragraph', 'a'),
        ('short two', ('A','H2','H3'), 3, 'paragraph', 'a'),
        ('| A | B |\n|---|---|\n| x | y |', ('A','H2','H3'), 3, 'table', 'a'),
        ('A', ('A',), 4, 'heading', 'b'), ('other', ('A',), 4, 'paragraph', 'b')])
    chunks, maps = Chunker().split_with_provenance(parsed, 'v')
    assert len(chunks) == 2
    assert {s.section_path for s in parsed.spans if s.span_id in maps[0].span_ids} >= {
        ('A','H2'), ('A','H2','H3')}
    assert_reconstruction(parsed, chunks, maps)
    for c in chunks:
        if '| x |' in c.text:
            assert '| A | B |\n|---|---|\n| x | y |' in c.text
    with pytest.raises(IngestionError, match='tabela_excede_teto'):
        Chunker(ChunkConfig(1, 2)).split(parsed, 'v')


def test_C05_prefers_near_target_structural_boundary():
    parsed = document([('a'*100, ('A',), 1, 'paragraph', 'a'),
        ('H2', ('A','H2'), 1, 'heading', 'a'),
        ('b'*100, ('A','H2'), 1, 'paragraph', 'a'),
        ('c'*300, ('A','H2'), 1, 'paragraph', 'a')])
    chunks, maps = Chunker(ChunkConfig(50, 80)).split_with_provenance(parsed, 'v')
    assert chunks[0].end_offset == parsed.spans[5].end
    assert_reconstruction(parsed, chunks, maps)


def provenance(page, start, end):
    return ProvenanceItem(page_no=page, charspan=(start,end),
        bbox=BoundingBox(l=0,t=1,r=1,b=0))


def test_C03_all_docling_charspans_and_unknown_not_proportional():
    doc = DoclingDocument(name='real-model')
    doc.add_text(label=DocItemLabel.TEXT, text='same😀e\u0301same')
    doc.texts[-1].prov = [provenance(2,0,7), provenance(3,7,11)]
    parser = DoclingParser()
    parsed = parser._adapt(doc, 'source.pdf', 'v')
    assert parsed.text == 'same😀e\u0301same'
    assert [(s.start,s.end,s.page) for s in parsed.spans] == [(0,7,2),(7,11,3)]
    chunks, maps = Chunker().split_with_provenance(parsed,'v')
    for start,end,pages in [(7,11,{3}),(0,4,{2}),(5,9,{2,3})]:
        selection = CitationSpanSelection(chunk_id=chunks[0].chunk_id,
            canonical_id=parsed.canonical.canonical_id,start=start,end=end,evidence=parsed.text[start:end])
        assert {s.page for s in resolve_citation_spans(selection,parsed.canonical,parsed.spans,chunks[0],maps[0])} == pages
    doc.texts[-1].prov = [provenance(2,0,11),provenance(3,0,11)]
    unknown = parser._adapt(doc,'source.pdf','u')
    assert all(s.page is None and s.provenance == 'unknown' for s in unknown.spans)
    doc.texts[-1].prov = [provenance(2,0,4),provenance(3,7,11)]
    gaps = parser._adapt(doc,'source.pdf','g')
    assert [(s.start,s.end,s.page) for s in gaps.spans] == [(0,4,2),(4,7,None),(7,11,3)]
    no_pages = parser._adapt(doc,'source.docx','d')
    assert all(s.page is None for s in no_pages.spans)


def test_C04_rejects_invalid_source_or_version():
    parsed = document([('e\u0301😀', ('A',), None,'paragraph','a')])
    with pytest.raises(ValueError):
        ParsedDocument(parsed.canonical, (), None)
    with pytest.raises(ValueError, match='versão'):
        Chunker().split(parsed,'other')
    for value in (0,-1,True,1.5):
        with pytest.raises(ValueError):
            ChunkConfig(target_tokens=value)
    with pytest.raises(ValueError, match='separador'):
        ChunkConfig(1,1)
    with pytest.raises(ValueError):
        ChunkConfig(10,20,overlap_tokens=20)


# Arquivos sintéticos equivalentes, construídos em memória sem dependências novas.
def fixtures(title='Biology'):
    parts = [(title,24),('Cells contain genetic material.',12),
             ('Details',16),('Membranes protect cells.',12)]
    md = f'# {title}\n\nCells contain genetic material.\n\n## Details\n\nMembranes protect cells.\n'.encode()
    ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    paragraphs = ''.join('<w:p>' + (f'<w:pPr><w:pStyle w:val="Heading{1 if size==24 else 2}"/></w:pPr>' if size!=12 else '') + f'<w:r><w:t>{text}</w:t></w:r></w:p>' for text,size in parts)
    with BytesIO() as output:
        with ZipFile(output,'w') as z:
            z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
            z.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr('word/_rels/document.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
            z.writestr('word/styles.xml',f'<w:styles xmlns:w="{ns}"><w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style></w:styles>')
            z.writestr('word/document.xml',f'<w:document xmlns:w="{ns}"><w:body>{paragraphs}</w:body></w:document>')
        docx = output.getvalue()
    commands = ''.join(f'BT /F1 {size} Tf 60 {750-i*65} Td ({text}) Tj ET\n' for i,(text,size) in enumerate(parts)).encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',b'<< /Length '+str(len(commands)).encode()+b' >>\nstream\n'+commands+b'endstream',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    pdf, offsets = b'%PDF-1.4\n', [0]
    for i,obj in enumerate(objects,1):
        offsets.append(len(pdf)); pdf += f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n'
    xref = len(pdf)
    pdf += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    pdf += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets[1:])
    pdf += f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return {'md':md,'docx':docx,'pdf':pdf}


def test_C03_C05_real_equivalent_formats_and_H2():
    parser, values = DoclingParser(), []
    for ext,data in fixtures().items():
        parsed = parser.parse(data,'study.'+ext,'v-'+ext)
        assert next(s for s in parsed.spans if parsed.text[s.start:s.end]=='Details').section_path == ('Biology','Details')
        assert parsed.title == 'Biology'
        assert all(s.page is None for s in parsed.spans) if ext!='pdf' else any(s.page==1 for s in parsed.spans)
        chunks,maps = Chunker().split_with_provenance(parsed,'v-'+ext)
        assert len(chunks)==1
        assert_reconstruction(parsed,chunks,maps)
        values.append(parsed.text)
    assert len(set(values))==1


@pytest.mark.parametrize('shared',[True,False])
@pytest.mark.parametrize('ext',['md','docx','pdf'])
def test_C08_real_threads_lazy_reuse_isolated(shared, ext, monkeypatch):
    parsers = [DoclingParser(),DoclingParser()]
    if shared:
        parsers[1]=parsers[0]
    barrier, state_lock = Barrier(2), Lock()
    counts, active = {}, {}
    for parser in set(parsers):
        original = parser._build_converter
        def build(parser=parser, original=original):
            with state_lock:
                counts[parser]=counts.get(parser,0)+1
            converter=original()
            real_convert=converter.convert
            def convert(*args,**kwargs):
                with state_lock:
                    active[parser]=active.get(parser,0)+1
                    assert active[parser]==1
                try:
                    return real_convert(*args,**kwargs)
                finally:
                    with state_lock:
                        active[parser]-=1
            monkeypatch.setattr(converter,'convert',convert)
            return converter
        monkeypatch.setattr(parser,'_build_converter',build)
    def run(i):
        barrier.wait(timeout=20)
        name = f'Unique{i}'
        data = fixtures(name)[ext]
        results = [parsers[i].parse(data,'source.'+ext,f'v-{i}') for _ in range(2)]
        assert results[0].canonical.text_sha256==results[1].canonical.text_sha256
        assert all(name in p.text and f'Unique{1-i}' not in p.text for p in results)
        assert all(any(s.section_path==(name,'Details') for s in p.spans) for p in results)
        for parsed in results:
            chunks, maps = Chunker().split_with_provenance(parsed, f'v-{i}')
            assert_reconstruction(parsed, chunks, maps)
        return results[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,range(2)))
    assert len({p.canonical.text_sha256 for p in results})==2
    assert len(counts)==(1 if shared else 2)
    assert set(counts.values())=={1}


def test_C06_overlap_zero_and_tiny_budget_separators():
    parsed = document([('😀e\u0301'*30, ('A',),None,'paragraph','a'),
                       ('漢字'*30, ('A',),None,'paragraph','a')])
    for config in (ChunkConfig(1,2),ChunkConfig(4,6,overlap_tokens=0)):
        chunks,maps = Chunker(config).split_with_provenance(parsed,'v')
        assert all(count_tokens(c.text)<=config.max_tokens for c in chunks)
        assert all(not m.overlap_ranges for m in maps)
        assert_reconstruction(parsed,chunks,maps)


def test_C05_real_repeated_H1_H2_H3():
    parser = DoclingParser()
    parsed = parser.parse(b'# A\n\n## Small\n\nOne\n\n### Tiny\n\nTwo\n\n# A\n\nOther', 'repeat.md','v')
    chunks,maps = Chunker().split_with_provenance(parsed,'v')
    assert len(chunks)==2
    assert {s.h1_id for s in parsed.spans if s.kind=='heading' and len(s.section_path)==1}=={'h1:1','h1:2'}
    assert any(s.section_path==('A','Small','Tiny') for s in parsed.spans)
    assert_reconstruction(parsed,chunks,maps)
