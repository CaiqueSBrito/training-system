"""Uma execução CLI real em PG/Qdrant descartáveis, sem dublês ou retentativas.

Não é teste coletado automaticamente. Mantém sandbox somente com --keep-sandbox,
para coleta da avaliação; manifesto identifica exclusivamente os recursos do run.
"""
import argparse
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
from urllib.request import urlopen
from uuid import uuid4

from sqlalchemy import create_engine,select,text
from training_lineage import create_lineage_engine,migrate,tables as t
from training_ingestion import Ingestor
from training_ingestion.indexing import IndexingPipeline
from training_llm import read_settings
from training_generation.chat import RequestAdmission


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--artifacts',required=True)
    parser.add_argument('--keep-sandbox',action='store_true')
    args=parser.parse_args()
    output=Path(args.artifacts).resolve();output.mkdir(parents=True,exist_ok=True)
    run_id='t021-real-'+uuid4().hex
    database='test_'+run_id.replace('-','_')
    owner=run_id;session=run_id+'-session';container=run_id+'-qdrant'
    admin=create_lineage_engine();db=None;processor=None;started=False
    manifest={'run_id':run_id,'database_name':database,'container_name':container,'owner_id':owner,
        'session_id':session,'case_id':'t021-cli-membrana-v1','status':'Não executado','files':{},'resources_retained':args.keep_sandbox}
    try:
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
            c.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0'))
        db=create_engine(admin.url.set(database=database),hide_parameters=True)
        migrate(db)
        with db.connect() as c:
            manifest['migration_head']=c.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        snapshot=['packages/generation/src/training_generation/'+n for n in ('chat.py','cli.py','agents.py','validation.py','settings.py')]
        snapshot+=['packages/lineage/src/training_lineage/chat.py','packages/lineage/src/training_lineage/tables.py',
            'packages/lineage/src/training_lineage/migrations/versions/0005_chat_audit.py',
            'packages/retrieval/src/training_retrieval/service.py','packages/retrieval/calibration/bge-v1.json',
            'packages/ingestion/src/training_ingestion/protocol.py','packages/contracts/src/training_contracts/rendering.py']
        snapshot+=sorted(str(f) for f in Path('packages/generation/src/training_generation/prompts').rglob('*') if f.is_file() and '__pycache__' not in f.parts)
        manifest['snapshot_sha256']={f:sha256(Path(f).read_bytes()).hexdigest() for f in snapshot}
        subprocess.run(['docker','run','--pull=never','--rm','-d','--name',container,'-p','127.0.0.1::6333',
            '-e','QDRANT__TELEMETRY_DISABLED=true','qdrant/qdrant:v1.20.2'],check=True,capture_output=True)
        started=True
        url='http://'+subprocess.check_output(['docker','port',container,'6333/tcp'],text=True).strip()
        manifest['qdrant_url']=url
        deadline=time.monotonic()+30
        while True:
            try:
                with urlopen(url+'/readyz',timeout=1) as response: assert response.status==200
                break
            except (OSError,AssertionError):
                if time.monotonic()>deadline: raise RuntimeError('sandbox unavailable') from None
                time.sleep(.2)
        with tempfile.TemporaryDirectory(prefix='t021-real-') as temporary:
            envfile=Path(temporary)/'.env'
            values=read_settings()
            keys={'GEMINI_API_KEY','OPENROUTER_API_KEY','GENERATION_MODEL','INPUT_JUDGE_MODEL','OUTPUT_JUDGE_MODEL',
                'INGESTION_JUDGE_MODEL','EMBEDDING_MODEL','EMBEDDING_DIM','EMBEDDING_FORMAT_VERSION','LLM_TIMEOUT_SECONDS',
                'CHAT_MAX_ITEMS','CHAT_MAX_INPUT_CHARS'}
            selected={k:v for k,v in values.items() if k.startswith('APP_POSTGRES_') or k in keys}
            selected.update(APP_POSTGRES_DB=database,PROMPT_SOURCE='offline',QDRANT_URL=url)
            descriptor=os.open(envfile,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w') as stream:
                stream.write('\n'.join(k+'='+shlex.quote(str(v)) for k,v in selected.items()))
            from training_llm import ModelSettings,GeminiModels
            from training_ingestion.indexing import QdrantIndex
            from qdrant_client import QdrantClient
            model_settings=replace(ModelSettings.from_env(str(envfile)),qdrant_url=url)
            index=QdrantIndex(QdrantClient(url=url,cloud_inference=True),model_settings)
            index.ensure_collection()
            processor=IndexingPipeline(GeminiModels(model_settings),index)
            from google import genai
            from google.genai import types
            processor.model_client.client.close()
            processor.model_client.client=genai.Client(api_key=processor.model_client.settings.api_key,
                http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))
            admission=RequestAdmission(6,4)
            embed=processor.model_client.embed_document
            def admitted(*values):
                admission.acquire();return embed(*values)
            processor.model_client.embed_document=admitted
            source=output/'source.md'
            source.write_text('# Membrana celular\n\nA membrana celular delimita a célula e controla a entrada e a saída de substâncias. Sua permeabilidade seletiva ajuda a manter o equilíbrio interno da célula.',encoding='utf-8')
            before=time.monotonic()
            result=Ingestor(db,processor=processor).ingest(source,owner)
            manifest.update(document_version_ids=[result.version.document_version_id],source_sha256=result.version.sha256,
                parser_version=result.version.parser_version,ingestion_seconds=time.monotonic()-before,ingestion_calls_admitted=admission.calls)
            time.sleep(6)  # Admissão entre embedding de ingestão e o novo processo CLI.
            message='Crie um flashcard objetivo sobre a função da membrana celular, usando uma citação literal curta da fonte.'
            manifest['request']={'message_sha256':sha256(message.encode()).hexdigest(),'count':1,
                'document_version_ids':[result.version.document_version_id],'section_prefix':[]}
            manifest['budget']={'cli_max_model_calls':12,'cli_request_interval_seconds':6,'ingestion_max_calls':4,
                'sdk_attempts':1,'litellm_num_retries':0,'litellm_max_retries':0,'manual_retries':0}
            command=[str(Path('.venv/bin/training-chat').resolve()),'--owner',owner,'--session',session,
                '--version',result.version.document_version_id,'--message',message,
                '--count','1','--env-file',str(envfile),'--calibration','packages/retrieval/calibration/bge-v1.json',
                '--diagnostics',str(output/'diagnostics.json'),'--evaluation-capture',str(output/'e8-capture.json'),
                '--request-interval','6','--max-model-calls','12']
            cli=subprocess.run(command,capture_output=True,text=True,timeout=300,
                env=dict(os.environ,APP_POSTGRES_DB=database,QDRANT_URL=url,PROMPT_SOURCE='offline'))
            (output/'response.json').write_text(cli.stdout,encoding='utf-8')
            (output/'progress.txt').write_text(cli.stderr,encoding='utf-8')
            manifest['cli_exit_code']=cli.returncode
            if (output/'diagnostics.json').exists():
                # Só categorias/HTTP status (sem texto): 429/503 viram bloqueio registrado, sem retry.
                manifest['provider_errors']=json.loads((output/'diagnostics.json').read_text(encoding='utf-8')).get('errors',[])
            try:
                from training_contracts import ChatResponse
                response=ChatResponse.model_validate_json(cli.stdout)
                manifest.update(chat_turn_id=response.chat_turn_id,trace_id=response.trace_id,
                    delivered_items=len(response.itens),response_status=response.status.value)
                if response.itens:
                    with db.connect() as c:
                        actual=c.execute(select(t.chat_turn.c.response).where(t.chat_turn.c.chat_turn_id==response.chat_turn_id)).scalar_one()
                        assert actual==response.model_dump(mode='json')
                        calls=c.execute(select(t.chat_generation_call.c.kind,t.chat_generation_call.c.status).where(
                            t.chat_generation_call.c.chat_turn_id==response.chat_turn_id)).all()
                        manifest['persisted_e8_calls']=[{'kind':k,'status':v} for k,v in calls]
                        manifest['commit_verified_by']='leitura em conexão separada de chat_turn.response igual ao stdout'
                    assert (output/'e8-capture.json').stat().st_mode & 0o777==0o600
                manifest['status']='Passou' if response.itens and cli.returncode==0 else 'Falhou'
            except Exception:
                manifest['status']='Falhou'
                manifest['error_kind']='cli_response_or_persistence'
    except Exception as exc:
        manifest.update(status='Falhou',error_kind=type(exc).__name__,
            http_status=getattr(exc,'status_code',None),provider_category=getattr(exc,'category',None))
    finally:
        if processor is not None:
            processor.index.client.close();processor.model_client.client.close()
        if db is not None: db.dispose()
        if not args.keep_sandbox:
            if started: subprocess.run(['docker','rm','-f',container],capture_output=True)
            with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
                c.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin.dispose()
        for filename in ('source.md','response.json','diagnostics.json','e8-capture.json','progress.txt'):
            path=output/filename
            if path.exists(): manifest['files'][filename]=sha256(path.read_bytes()).hexdigest()
        (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:manifest.get(k) for k in ('run_id','status','cli_exit_code','response_status','delivered_items','error_kind','http_status')}))
    return 0 if manifest['status']=='Passou' else 1


if __name__=='__main__':
    raise SystemExit(main())
