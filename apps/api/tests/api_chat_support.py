"""T-025: fronteiras de modelos controladas; SQL, ingestão e pipeline reais."""
import asyncio
import json
import subprocess
import time
from types import SimpleNamespace
from urllib.request import urlopen
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from training_contracts import Classification, InputVerdict, RawGenerationOutput, SearchPlan, OutputJudgeResult
from training_generation import GenerationMetadata, GenerationError
from training_generation.chat import ChatPipeline
from training_ingestion import Ingestor
from training_ingestion.indexing import IndexingPipeline, QdrantIndex
from training_llm import ModelSettings


class Models:
    def __init__(self):
        self.settings = SimpleNamespace(max_input_chars=8000, max_items=20)
        self.calls = []
        self.fail = False
        self.slow = False
        self.entered = asyncio.Event()
        self.cancelled = False
        self.observe = None

    def metadata(self):
        return GenerationMetadata(models={s: 'test-model' for s in ('E2', 'E3', 'E4', 'E8', 'E10')},
            prompt_versions={p: 'test-v1' for p in ('input_judge', 'classifier', 'pedagogue',
                                                   'generation', 'output_judge', 'output_rubric')},
            prompt_source='offline')

    async def input_verdict(self, prompt):
        self.calls.append('E2')
        if self.observe:
            self.observe()
        self.entered.set()
        if self.slow:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        # E3 deve concluir primeiro: o transporte precisa ordenar o progresso.
        await asyncio.sleep(.02)
        return InputVerdict(malicioso=False, motivo='controle do teste')

    async def classify(self, prompt):
        self.calls.append('E3')
        return Classification(intencao='gerar_flashcards', modo='consulta')

    async def plan(self, *args):
        return SearchPlan(objetivos=['Membrana celular'], bloom_level='lembrar', consultas=['membrana'])

    async def generate(self, prompt, classification, plan, context, count, *args, **kwargs):
        self.calls.append('E8')
        if self.fail:
            raise GenerationError('timeout', stage='E8')
        chunk = context.chunks[0]
        return RawGenerationOutput(itens=[dict(tipo='flashcard', front='Qual a função da membrana celular?',
            back=chunk.text, bloom_level='lembrar', citations=[dict(chunk_ref=chunk.ref, evidence=chunk.text)])])

    async def judge(self, items, context):
        return OutputJudgeResult(vereditos=[dict(item_index=i, fundamentado=True, conforme_rubrica=True)
                                           for i in range(len(items))])

    def embed_document(self, *args):
        return [1.] + [0.] * 3071

    def embed_query(self, *args):
        return [1.] + [0.] * 3071


class Reranker:
    cutoff = .5

    def score(self, queries, chunks):
        return [.99] * len(chunks)


@pytest.fixture(scope='module')
def chat_qdrant():
    name = 'test-t025-' + uuid4().hex
    subprocess.run(['docker', 'run', '--pull=never', '--rm', '-d', '--name', name,
                    '-p', '127.0.0.1::6333', '-e', 'QDRANT__TELEMETRY_DISABLED=true',
                    'qdrant/qdrant:v1.20.2'], check=True, capture_output=True)
    try:
        url = 'http://' + subprocess.check_output(['docker', 'port', name, '6333/tcp'], text=True).strip()
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/readyz', timeout=1) as response:
                    assert response.status == 200
                break
            except (OSError, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError('Qdrant descartável indisponível') from None
                time.sleep(.1)
        yield url
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True)


@pytest.fixture
def chat_box(database, app, api, alice, chat_qdrant, tmp_path):
    models = Models()
    client = QdrantClient(url=chat_qdrant, cloud_inference=True)
    index = QdrantIndex(client, ModelSettings(qdrant_url=chat_qdrant))
    index.ensure_collection()
    pipelines = []

    def factory(*, progress):
        pipeline = ChatPipeline(database, models, index, models, Reranker(), progress=progress)
        pipelines.append(pipeline)
        return pipeline

    # Ponto público de injeção proposto; não substitui ChatPipeline.run nem repositórios.
    app.state.chat_pipeline_factory = factory
    try:
        source = tmp_path / 'biologia.md'
        source.write_text('# Biologia\n\nA membrana celular controla a entrada de substâncias.', encoding='utf-8')
        result = Ingestor(database, processor=IndexingPipeline(models, index)).ingest(source, str(alice['id']))
        session = api.login('alice@example.com')
        yield SimpleNamespace(models=models, factory=factory, version=result.version.document_version_id,
                              owner=str(alice['id']), session=session, pipelines=pipelines)
    finally:
        for pipeline in pipelines:
            pipeline.provider.shutdown()
        client.delete_collection(index.collection_name)
        client.close()


def events(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-cache'
    assert response.headers['x-accel-buffering'] == 'no'
    result = []
    for frame in response.text.replace('\r\n', '\n').split('\n\n'):
        lines = [line for line in frame.splitlines() if line and not line.startswith(':')]
        if not lines:
            continue
        name = [line[6:].strip() for line in lines if line.startswith('event:')]
        data = [line[5:].lstrip() for line in lines if line.startswith('data:')]
        assert len(name) == 1 and data, frame
        result.append((name[0], json.loads('\n'.join(data))))
    assert result and result[-1][0] == 'final'
    assert sum(name == 'final' for name, _ in result) == 1
    assert all(name == 'progress' for name, _ in result[:-1])
    return result


def post_chat(api, box, message='Gere um flashcard', **kwargs):
    return api.request('POST', '/chat', session=box.session, csrf=box.session.csrf,
                       json={'session_id': 'chat-session', 'mensagem': message, **kwargs})
