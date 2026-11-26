"""Orçamento real de chamadas no transporte LiteLLM do produto (achado R1 da reavaliação T-022).

Fronteira real: GenerationService._completion -> litellm.acompletion -> cliente OpenAI/httpx, contra
um servidor HTTP local descartável que responde 429/503. Nenhuma API externa é chamada; o único
dublê é o servidor local (dentro deste arquivo de teste).
"""
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault('LITELLM_LOCAL_MODEL_COST_MAP', 'True')  # sem download do mapa de custos

import pytest

from training_contracts import ContextChunk, Flashcard, NumberedContext
from training_generation.agents import GenerationError, GenerationService
from training_generation.settings import GenerationSettings

from conftest import env


@pytest.fixture
def provider_server():
    state={'requests':0,'status':503}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length') or 0))
            state['requests']+=1
            body=json.dumps({'error':{'message':'test overload','type':'server_error','code':state['status']}}).encode()
            self.send_response(state['status'])
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
            # retry-after curto: com retentativa do SDK ativa, a segunda requisição chegaria aqui.
            self.send_header('retry-after','0');self.end_headers();self.wfile.write(body)
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        yield state,f'http://127.0.0.1:{server.server_address[1]}/v1'
    finally:
        server.shutdown();server.server_close()


@pytest.mark.parametrize('status',[429,503])
@pytest.mark.parametrize('stage',['E2','E10'])
def test_litellm_transport_sends_exactly_one_request_on_429_503(provider_server,monkeypatch,status,stage):
    state,base=provider_server;state['status']=status
    monkeypatch.setenv('OPENROUTER_API_BASE',base)
    monkeypatch.setenv('NO_PROXY','127.0.0.1');monkeypatch.setenv('no_proxy','127.0.0.1')
    settings=GenerationSettings.from_mapping(env(INPUT_JUDGE_MODEL='openrouter/anthropic/claude-haiku-5.5'))
    service=GenerationService(settings)
    context=NumberedContext(chunks=[ContextChunk(ref='C1',chunk_id='chunk-a',page=1,section_path=['1'],
        text='A clorofila absorve a luz na fase clara.')])
    item=Flashcard(tipo='flashcard',front='O que absorve a luz?',back='A clorofila.',bloom_level='lembrar',
        citations=[{'chunk_ref':'C1','evidence':'A clorofila absorve a luz'}])
    call=service.input_verdict('Gere flashcards') if stage=='E2' else service.judge([item],context)
    with pytest.raises(GenerationError) as caught:
        asyncio.run(call)
    assert state['requests']==1, f'{state["requests"]} requisições para um orçamento de 1'
    assert caught.value.status_code==status
