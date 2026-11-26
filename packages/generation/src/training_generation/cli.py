"""CLI local confiável. Owner explícito é identidade declarada pelo operador, não autenticação."""
import argparse
import asyncio
import json
import logging
import os
import sys
from opentelemetry.sdk.trace import TracerProvider
from training_lineage import create_lineage_engine
from training_ingestion.indexing import IndexingPipeline
from training_retrieval import LocalReranker
from .agents import GenerationService
from .settings import GenerationSettings
from .chat import ChatPipeline, RequestAdmission, AdmittedQueries


def parser():
    result=argparse.ArgumentParser(description='Chat RAG local confiável; não implementa autenticação.')
    result.add_argument('--owner',required=True)
    result.add_argument('--session',required=True)
    result.add_argument('--version',action='append',required=True,dest='versions')
    result.add_argument('--section',action='append',default=[],dest='sections')
    result.add_argument('--message',required=True)
    result.add_argument('--count',type=int,default=5)
    result.add_argument('--calibration',required=True,help='Manifesto real BGE calibrado da T018')
    result.add_argument('--request-interval',type=float,default=6)
    result.add_argument('--max-model-calls',type=int,default=12)
    result.add_argument('--env-file',default='.env')
    result.add_argument('--evaluation-capture',help='Artefato opt-in com contexto/conteúdo original para avaliação controlada')
    result.add_argument('--diagnostics',help='Arquivo JSON de latências/modelos/prompts, sem texto ou segredo')
    return result


def _fail(message,code=2):
    print(json.dumps({'status':'erro','mensagem':message},ensure_ascii=False),file=sys.stderr)
    return code


def _write_capture(path,payload):
    # S3: nunca segue symlink nem sobrescreve; o arquivo novo nasce 0600.
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    os.fchmod(descriptor,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
        json.dump(payload,stream,ensure_ascii=False,indent=2)


def main(argv=None):
    arguments=parser();args=arguments.parse_args(argv)
    if not all(value.strip() for value in [args.owner,args.session,*args.versions]):
        arguments.error('owner/session/version não podem ser vazios')
    if not 1<=args.count<=20: arguments.error('count deve ser 1..20')
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(message)s')
    # Recusa antecipada (antes de qualquer chamada externa); O_EXCL|O_NOFOLLOW repete a garantia na escrita.
    if args.evaluation_capture and os.path.lexists(args.evaluation_capture):
        return _fail('Caminho de captura já existe ou é link; escolha um arquivo novo.')
    engine=None;processor=None;provider=None
    try:
        settings=GenerationSettings.from_env(args.env_file)
        if args.count>settings.max_items: arguments.error('count excede CHAT_MAX_ITEMS')
        engine=create_lineage_engine(args.env_file)
        # Não migra banco compartilhado: esquema precisa ser provisionado pelo operador.
        processor=IndexingPipeline.from_env(args.env_file)
        from google import genai
        from google.genai import types
        processor.model_client.client.close()
        processor.model_client.client=genai.Client(api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))
        admission=RequestAdmission(args.request_interval,args.max_model_calls)
        provider=TracerProvider()
        generation=GenerationService(settings,tracer_provider=provider)
        def progress(record):
            print(json.dumps(record,ensure_ascii=False),file=sys.stderr,flush=True)
        pipeline=ChatPipeline(engine,generation,processor.index,AdmittedQueries(processor.model_client,admission),
            LocalReranker(args.calibration),tracer_provider=provider,progress=progress,admission=admission,evaluation_capture=bool(args.evaluation_capture))
        response=asyncio.run(pipeline.run(args.owner,args.session,args.message,
            document_version_ids=args.versions,section_prefix=args.sections,count=args.count))
        print(response.model_dump_json(),flush=True)
        pipeline.last_execution['model_calls_admitted']=admission.calls
        pipeline.last_execution['embedding_key']=processor.index.settings.embedding_key
        pipeline.last_execution['reranker']={k:pipeline.reranker.manifest[k] for k in ('model','revision','cutoff','manifest_sha256') if k in pipeline.reranker.manifest}
        pipeline.last_execution['reranker']['manifest_sha256']=pipeline.reranker.manifest_sha256
        if args.diagnostics:
            from pathlib import Path
            Path(args.diagnostics).write_text(json.dumps(pipeline.last_execution,ensure_ascii=False,indent=2),encoding='utf-8')
        if args.evaluation_capture:
            try: _write_capture(args.evaluation_capture,pipeline.last_evaluation)
            except OSError: return _fail('Captura não gravada: caminho existente, link ou sem permissão.')
        return 1 if response.status.value=='erro' else 0
    except (KeyboardInterrupt,asyncio.CancelledError):
        # O turno cancelado é persistido como erro pela pipeline; nada de traceback no stderr.
        return _fail('Turno cancelado.',130)
    except Exception:
        # Exceções de SDK/SQL não são diagnósticos seguros para stderr.
        return _fail('Configuração ou infraestrutura indisponível.')
    finally:
        for close in ((provider.shutdown,) if provider is not None else ())+(
                (processor.index.client.close,processor.model_client.client.close) if processor is not None else ())+(
                (engine.dispose,) if engine is not None else ()):
            try: close()
            except Exception: pass


if __name__=='__main__':
    raise SystemExit(main())
