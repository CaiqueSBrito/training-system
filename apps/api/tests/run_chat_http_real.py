"""T-025: uma prova HTTP real, recursos descartáveis, sem retry ou dublês.

Executar manualmente após a suíte offline; nunca coletado por pytest.
"""
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from urllib.request import urlopen
from uuid import uuid4
from hashlib import sha256

import httpx
import uvicorn
from sqlalchemy import create_engine, select, text
from training_api.app import create_app
from training_api.config import ApiSettings
from training_contracts import ChatResponse
from training_ingestion import Ingestor
from training_ingestion.indexing import IndexingPipeline
from training_lineage import create_lineage_engine, migrate, tables as t


OUTPUT = Path('governanca/mudancas/2026-11-26-apps-api-autenticacao/evidencias/T-025/real-http')


def main():
    logging.basicConfig(level=logging.WARNING)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    run = 't025-' + uuid4().hex
    database = 'test_' + run.replace('-', '_')
    container = run + '-qdrant'
    manifest = {'run_id': run, 'status': 'Não executado', 'database_name': database,
                'container_name': container, 'chat_requests': 0, 'retry': False,
                'langfuse': 'desligado; tracing OTel local', 'cleanup': {}}
    admin = db = server = thread = pipeline = sock = None
    created = started = False
    previous = {key: os.environ.get(key) for key in ('QDRANT_URL', 'PROMPT_SOURCE', 'CHAT_RERANKER_CALIBRATION')}
    try:
        admin = create_lineage_engine()
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            connection.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0'))
        created = True
        db = create_engine(admin.url.set(database=database), hide_parameters=True)
        migrate(db)
        subprocess.run(['docker', 'run', '--pull=never', '--rm', '-d', '--name', container,
            '-p', '127.0.0.1::6333', '-e', 'QDRANT__TELEMETRY_DISABLED=true',
            'qdrant/qdrant:v1.20.2'], check=True, capture_output=True)
        started = True
        url = 'http://' + subprocess.check_output(['docker', 'port', container, '6333/tcp'], text=True).strip()
        deadline = time.monotonic() + 30
        while True:
            try:
                with urlopen(url + '/readyz', timeout=1) as response:
                    assert response.status == 200
                break
            except (OSError, AssertionError):
                if time.monotonic() > deadline:
                    raise RuntimeError('Qdrant readiness') from None
                time.sleep(.2)
        os.environ.update(QDRANT_URL=url, PROMPT_SOURCE='offline',
                          CHAT_RERANKER_CALIBRATION='packages/retrieval/calibration/bge-v1.json')
        app = create_app(ApiSettings(database_url=db.url, attempt_key_secret=secrets.token_hex(32),
                                     cookie_secure=False))
        # Cookie Secure desativado somente no servidor HTTP loopback descartável.
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        address = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', log_level='warning', access_log=False))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 60
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError('HTTP readiness')
            time.sleep(.1)
        factory = getattr(app.state, 'chat_pipeline_factory', None)
        if factory is None:
            manifest.update(status='Bloqueado', reason='Composição real indisponível; nenhuma chamada de chat')
            return
        with httpx.Client(base_url=address, timeout=480, trust_env=False) as client:
            password = secrets.token_urlsafe(24)
            email = run + '@example.com'
            registration = client.post('/auth/register', json={'email': email, 'password': password})
            manifest['register_http_status'] = registration.status_code
            assert registration.status_code == 201
            owner = registration.json()['id']
            manifest['owner_id'] = owner
            login = client.post('/auth/login', data={'username': email, 'password': password})
            manifest['login_http_status'] = login.status_code
            assert login.status_code == 200
            csrf = login.json()['csrf_token']  # nunca persistido/logado
            pipeline = factory(progress=None)
            # Usa o mesmo cliente real sem retry que foi montado pelo lifespan.
            processor = IndexingPipeline(pipeline.model.model, pipeline.index)
            source = OUTPUT / 'source.md'
            source.write_text('# Membrana celular\n\nA membrana celular delimita a célula e controla a entrada e a saída de substâncias. '
                'Sua permeabilidade seletiva ajuda a manter o equilíbrio interno. A bicamada de fosfolipídios '
                'forma uma barreira flexível. Proteínas da membrana transportam moléculas e recebem sinais. '
                'A difusão permite o movimento de partículas a favor do gradiente de concentração. '
                'O transporte ativo utiliza energia para mover substâncias contra esse gradiente.\n', encoding='utf-8')
            ingestion = Ingestor(db, processor=processor).ingest(source, owner)
            manifest['document_version_id'] = ingestion.version.document_version_id
            manifest['source_sha256'] = sha256(source.read_bytes()).hexdigest()
            manifest['chat_requests'] = 1
            with client.stream('POST', '/chat', headers={'X-CSRF-Token': csrf},
                    json={'session_id': run, 'mensagem': 'Gere flashcards sobre a função da membrana celular e seus transportes.'}) as response:
                manifest['chat_http_status'] = response.status_code
                manifest['headers'] = {key: response.headers.get(key) for key in
                                       ('content-type', 'cache-control', 'x-accel-buffering')}
                raw = response.read().decode()
            (OUTPUT / 'response.sse').write_text(raw, encoding='utf-8')
            assert manifest['chat_http_status'] == 200
            frames = []
            for block in raw.replace('\r\n', '\n').split('\n\n'):
                lines = block.splitlines()
                names = [line[6:].strip() for line in lines if line.startswith('event:')]
                values = [line[5:].lstrip() for line in lines if line.startswith('data:')]
                if names:
                    frames.append((names[0], json.loads('\n'.join(values))))
            finals = [value for name, value in frames if name == 'final']
            assert len(finals) == 1 and frames[-1][0] == 'final'
            result = ChatResponse.model_validate(finals[0])
            manifest.update(response_status=result.status.value, chat_turn_id=result.chat_turn_id,
                trace_id=result.trace_id, delivered_items=len(result.itens),
                progress=[value['stage'] for name, value in frames if name == 'progress'])
            with db.connect() as connection:
                stored = connection.execute(select(t.chat_turn.c.response).where(
                    t.chat_turn.c.owner_id == owner, t.chat_turn.c.chat_turn_id == result.chat_turn_id)).scalar_one()
                manifest['commit_verified'] = stored == result.model_dump(mode='json')
            # Consultas autenticadas também percorrem HTTP real, sem nova geração.
            manifest['turn_lineage_http_status'] = client.get('/lineage/turns/' + result.chat_turn_id).status_code
            manifest['item_lineage_http_status'] = [client.get('/lineage/items/' + item.item_id).status_code for item in result.itens]
            manifest['status'] = 'Passou' if (result.status.value == 'ok' and result.itens and
                manifest['commit_verified'] and manifest['turn_lineage_http_status'] == 200 and
                all(code == 200 for code in manifest['item_lineage_http_status'])) else 'Falhou'
    except Exception as exc:
        # Não serializar exceção/traceback SDK: pode conter parâmetros sensíveis.
        manifest.update(status='Falhou', error_kind=type(exc).__name__)
    finally:
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=30)
            manifest['cleanup']['http_stopped'] = not thread.is_alive()
        if sock is not None:
            sock.close()
        if db is not None:
            db.dispose()
        if created:
            with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
            manifest['cleanup']['database_removed'] = True
        if admin is not None:
            admin.dispose()
        if started:
            result = subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
            manifest['cleanup']['container_removed'] = result.returncode == 0
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        manifest['snapshot_sha256'] = {str(path): sha256(path.read_bytes()).hexdigest() for path in
            [*Path('apps/api/src/training_api').glob('*.py'), Path('packages/generation/src/training_generation/chat.py')]}
        manifest['files'] = {str(path.name): sha256(path.read_bytes()).hexdigest() for path in OUTPUT.iterdir()
                             if path.is_file() and path.name != 'manifest.json'}
        (OUTPUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({key: manifest.get(key) for key in ('run_id', 'status', 'chat_requests', 'response_status', 'delivered_items', 'error_kind', 'cleanup')}))


if __name__ == '__main__':
    main()
