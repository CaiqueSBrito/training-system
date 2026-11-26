"""I1–I3: coordenadas de codepoints sobre o canônico persistível (ADR-0015)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from io import BytesIO
import json
import re
from pathlib import Path
from threading import Lock

from training_contracts.linhagem import ChunkRecord, DocumentVersionRecord
from training_contracts.ingestao import (
    CanonicalSpan, CanonicalTextRecord, ChunkProvenanceRecord, TextRange,
    validate_canonical_spans, validate_chunk_provenance,
)


class IngestionError(ValueError):
    """Recusa explícita; nenhuma versão parcial deve ser persistida."""


def count_tokens(text: str) -> int:
    """Estimador utf8-4-v1: ceil(bytes UTF-8 / 4), não tokenizer Gemini."""
    return (len(text.encode("utf-8")) + 3) // 4


@dataclass(frozen=True)
class ParsedDocument:
    """Agregado de contratos concretos, pronto para persistência pelo T013."""
    canonical: CanonicalTextRecord
    spans: tuple[CanonicalSpan, ...]
    title: str | None = None

    def __post_init__(self):
        validate_canonical_spans(self.canonical, self.spans)

    @property
    def text(self) -> str:
        return self.canonical.text


@dataclass(frozen=True)
class ChunkConfig:
    target_tokens: int = 800
    max_tokens: int = 1500
    overlap_tokens: int | None = None

    def __post_init__(self):
        if (type(self.target_tokens) is not int or type(self.max_tokens) is not int
                or not 0 < self.target_tokens <= self.max_tokens):
            raise ValueError("Exige 0 < target_tokens <= max_tokens inteiros")
        if self.overlap_tokens is not None and (
                type(self.overlap_tokens) is not int or not 0 <= self.overlap_tokens < self.max_tokens):
            raise ValueError("overlap_tokens deve ser inteiro entre zero e max_tokens-1")
        if self.max_tokens < 2:
            raise ValueError('teto_insuficiente_para_codepoint_e_separador: max_tokens >= 2')

    @property
    def overlap_limit(self) -> int:
        return self.max_tokens // 2 if self.overlap_tokens is None else self.overlap_tokens

    @property
    def version(self) -> str:
        return (f"structure-v3:canonical-v2:utf8-4-v1:t{self.target_tokens}:m{self.max_tokens}:"
                f"last-paragraph-or-fragment-v2:o{self.overlap_limit}:h1-occurrence")


class DoclingParser:
    """Conversor lazy por instância; lock cobre construção, convert e adaptação."""

    canonical_version = "docling-order-v2:item-text-exact:table-markdown:separator-LF2:codepoints"

    def __init__(self):
        self._lock = Lock()
        self._converter = None

    @property
    def version(self):
        return f"docling-{version('docling')}:local-v2:all-charspans"

    def _build_converter(self):
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        options = PdfPipelineOptions(enable_remote_services=False)
        options.heading_hierarchy_options.enabled = True
        return DocumentConverter(
            allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.MD],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
        )

    def parse(self, data: bytes, filename: str, document_version_id: str) -> ParsedDocument:
        from docling.datamodel.base_models import DocumentStream, ConversionStatus
        try:
            with self._lock:
                if self._converter is None:
                    self._converter = self._build_converter()
                result = self._converter.convert(DocumentStream(name=filename, stream=BytesIO(data)))
                if result.status != ConversionStatus.SUCCESS:
                    raise IngestionError("falha_no_parse: conversão incompleta")
                return self._adapt(result.document, filename, document_version_id)
        except IngestionError:
            raise
        except Exception as exc:
            # Docling pode incluir dados do arquivo em suas exceções; não encadear.
            raise IngestionError(f"falha_no_parse: {type(exc).__name__}") from None

    @staticmethod
    def _page_segments(item, value, is_pdf, is_table):
        """Usa charspan apenas quando se refere ao texto exato do elemento.

        Cobertura ausente, charspan inválido e páginas sobrepostas são unknown.
        O Markdown de uma tabela não tem coordenadas de células: só uma página
        inequívoca permite atribuir a página a toda a unidade exportada.
        """
        if not is_pdf or not item.prov:
            return [(0, len(value), None)]
        if is_table:
            pages = {p.page_no for p in item.prov}
            return [(0, len(value), next(iter(pages)) if len(pages) == 1 else None)]
        intervals = []
        for prov in item.prov:
            start, end = prov.charspan
            if not 0 <= start < end <= len(value):
                return [(0, len(value), None)]
            intervals.append((start, end, prov.page_no))
        cuts = sorted({0, len(value), *(x for a,b,_ in intervals for x in (a,b))})
        segments = []
        for start, end in zip(cuts, cuts[1:]):
            pages = {p for a,b,p in intervals if a <= start and end <= b}
            page = next(iter(pages)) if len(pages) == 1 else None
            if segments and segments[-1][2] == page:
                segments[-1] = (segments[-1][0], end, page)
            else:
                segments.append((start, end, page))
        return segments

    def _adapt(self, doc, filename: str, document_version_id: str) -> ParsedDocument:
        from docling_core.types.doc import SectionHeaderItem, TableItem, TextItem, TitleItem
        spans, texts, titles = [], [], []
        offset, h1_number, h1_id, document_title = 0, 0, None, None
        # Identidade determinística inclui versão e receita; hash do texto é separado.
        canonical_id = sha256(json.dumps([document_version_id, self.canonical_version,
                                         self.version], separators=(',', ':')).encode()).hexdigest()

        def add(start, end, page, kind, path, h1):
            spans.append(CanonicalSpan(span_id=f'{canonical_id}:{len(spans)}',
                canonical_id=canonical_id, start=start, end=end, page=page,
                section_path=path, h1_id=h1, kind=kind,
                provenance='exact' if page is not None else 'unknown'))

        for item, _ in doc.iterate_items():
            heading = isinstance(item, (SectionHeaderItem, TitleItem))
            if isinstance(item, TableItem):
                value, kind = item.export_to_markdown(doc=doc), 'table'
            elif isinstance(item, TextItem):
                if item.label.value in {'page_header', 'page_footer'}:
                    continue
                value, kind = item.text, 'heading' if heading else 'paragraph'
            else:
                continue
            if not value or not value.strip():
                continue
            if heading:
                level = 1 if isinstance(item, TitleItem) else item.level + (
                    1 if Path(filename).suffix.lower() in {'.md', '.markdown'} else 0)
                titles = [(n,t) for n,t in titles if n < level]
                titles.append((level, value))
                if level == 1:
                    h1_number += 1
                    h1_id = f'h1:{h1_number}'
                    if document_title is None:
                        document_title = value
            if texts:
                texts.append('\n\n')
                add(offset, offset+2, None, 'separator', (), None)
                offset += 2
            texts.append(value)
            for start, end, page in self._page_segments(
                    item, value, Path(filename).suffix.lower() == '.pdf', kind == 'table'):
                add(offset+start, offset+end, page, kind, tuple(t for _,t in titles), h1_id)
            offset += len(value)
        if not spans:
            raise IngestionError('documento_sem_texto')
        text = ''.join(texts)
        canonical = CanonicalTextRecord(canonical_id=canonical_id,
            document_version_id=document_version_id, canonical_version=self.canonical_version,
            parser_version=self.version, text=text, text_sha256=sha256(text.encode()).hexdigest(),
            created_at=datetime.now(timezone.utc))
        return ParsedDocument(canonical, tuple(spans), document_title)


class Chunker:
    def __init__(self, config: ChunkConfig | None = None):
        self.config = config or ChunkConfig()

    @staticmethod
    def _byte_end(text: str, start: int, end: int, budget: int) -> int:
        low, high, fit = start+1, end, start
        while low <= high:
            middle = (low+high)//2
            if len(text[start:middle].encode('utf-8')) <= budget:
                fit, low = middle, middle+1
            else:
                high = middle-1
        if fit == start:
            raise IngestionError('teto_menor_que_codepoint')
        return fit

    def _units(self, document, spans, start, end):
        """Unidades internas são intervalos; nunca remontam texto ou proveniência."""
        units = []
        whole_budget = max(4, (self.config.max_tokens*4-2)//2)
        fragment_budget = min(whole_budget, max(4, self.config.target_tokens*2))
        for span in spans:
            if span.end <= start or span.start >= end:
                continue
            if span.kind == 'separator':
                if units and count_tokens(document.text[units[-1][0]:span.end]) <= self.config.max_tokens:
                    units[-1] = (units[-1][0], span.end, units[-1][2])
                else:
                    units.append((span.start, span.end, 'separator'))
                continue
            if span.kind == 'table':
                units.append((span.start, span.end, 'table'))
                continue
            budget = (whole_budget if len(document.text[span.start:span.end].encode('utf-8')) <= whole_budget
                      else fragment_budget)
            cursor = span.start
            while cursor < span.end:
                stop = self._byte_end(document.text, cursor, span.end, budget)
                kind = ('fragment' if span.kind == 'paragraph' and (cursor != span.start or stop != span.end)
                        else span.kind)
                if stop < span.end:
                    boundaries = list(re.finditer(r'\s+', document.text[cursor:stop]))
                    if boundaries:
                        stop = cursor + boundaries[-1].end()
                units.append((cursor, stop, kind))
                cursor = stop
        return units

    def split(self, document: ParsedDocument, document_version_id: str) -> list[ChunkRecord]:
        return self.split_with_provenance(document, document_version_id)[0]

    def split_with_provenance(self, document: ParsedDocument, document_version_id: str
                              ) -> tuple[list[ChunkRecord], tuple[ChunkProvenanceRecord, ...]]:
        if document.canonical.document_version_id != document_version_id:
            raise ValueError('versão do documento divergente do canônico')
        for span in document.spans:
            if span.kind == 'table' and count_tokens(document.text[span.start:span.end]) > self.config.max_tokens:
                raise IngestionError('tabela_excede_teto: tabela não é cortada')
        # H1 por ocorrência, nunca por igualdade do título nem por H2/H3.
        boundaries, current_h1 = [0], None
        for span in document.spans:
            if span.h1_id is not None and span.h1_id != current_h1:
                if span.start:
                    boundaries.append(span.start)
                current_h1 = span.h1_id
        boundaries.append(len(document.text))
        result, mappings = [], []

        def emit(pending):
            start, end = pending[0][0], pending[-1][1]
            selected = tuple(s for s in document.spans if s.start < end and start < s.end)
            first = next((s for s in selected if s.kind != 'separator'), selected[0])
            value = document.text[start:end]
            identity = json.dumps([document_version_id, document.canonical.canonical_id,
                document.canonical.text_sha256, self.config.version, start, end, value],
                ensure_ascii=False, separators=(',', ':'))
            chunk = ChunkRecord(chunk_id=sha256(identity.encode()).hexdigest(),
                document_version_id=document_version_id, chunker_version=self.config.version,
                page=first.page, section_path=list(first.section_path),
                start_offset=start, end_offset=end, text=value)
            overlap_end = min(end, result[-1].end_offset) if result else start
            mapping = ChunkProvenanceRecord(chunk_id=chunk.chunk_id,
                canonical_id=document.canonical.canonical_id, start=start, end=end,
                span_ids=tuple(s.span_id for s in selected), mapping_status='exact',
                overlap_ranges=(TextRange(start=start,end=overlap_end),) if overlap_end > start else (),
                created_at=document.canonical.created_at)
            validate_chunk_provenance(document.canonical, document.spans, chunk, mapping)
            result.append(chunk)
            mappings.append(mapping)

        for section_start, section_end in zip(boundaries, boundaries[1:]):
            pending = []
            for unit in self._units(document, document.spans, section_start, section_end):
                if pending:
                    current = count_tokens(document.text[pending[0][0]:pending[-1][1]])
                    candidate = count_tokens(document.text[pending[0][0]:unit[1]])
                    if current >= self.config.target_tokens or candidate > self.config.max_tokens:
                        emit(pending)
                        previous = pending[-1]
                        pending = ([previous] if len(pending) > 1
                            and previous[2] in {'paragraph','fragment'}
                            and count_tokens(document.text[previous[0]:previous[1]]) <= self.config.overlap_limit
                            and count_tokens(document.text[previous[0]:unit[1]]) <= self.config.max_tokens
                            else [])
                pending.append(unit)
            if pending:
                emit(pending)
        return result, tuple(mappings)


@dataclass(frozen=True)
class IngestionResult:
    version: DocumentVersionRecord
    chunks: list[ChunkRecord]
    reused: bool


class Ingestor:
    """Entrada pública única I1–I6; etapas remotas ficam fora de transações."""

    def __init__(self, engine, *, processor=None, parser: DoclingParser | None = None,
                 config: ChunkConfig | None = None,
                 max_file_bytes: int = 100 * 1024 * 1024):
        if max_file_bytes <= 0:
            raise ValueError("max_file_bytes deve ser positivo")
        if processor is None:
            raise ValueError('processor I4–I6 obrigatório')
        self.engine = engine
        self.parser = parser or DoclingParser()
        self.chunker = Chunker(config)
        self.max_file_bytes = max_file_bytes
        from .protocol import DurableIngestionProtocol
        self._protocol = DurableIngestionProtocol(engine, self.parser, self.chunker,
                                                  processor, max_file_bytes)

    def ingest(self, path: str | Path, owner_id: str, *, document_id: str | None = None) -> IngestionResult:
        return self._protocol.ingest(path, owner_id, document_id=document_id)

    def reclassify(self, owner_id: str, document_version_id: str):
        return self._protocol.reclassify(owner_id, document_version_id)
