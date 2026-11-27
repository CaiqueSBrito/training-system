"""Orquestração E1–E12 real; nenhum resultado é entregue antes do commit."""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from hashlib import sha256
from time import perf_counter
from uuid import uuid4

from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from sqlalchemy import select
from training_contracts import (ChatResponse, ChatTurnRecord, Classification, SearchPlan,
    SearchPlanRecord, ItemRecord, CitationRecord, VerdictRecord, RawGenerationOutput,
    NumberedContext, ContextChunk, MotivoRejeicao, MotivoRecusa, Intent, validar_item)
from training_contracts.chat import Source, DeliveredItem, Omission
from training_contracts.ingestao import CitationSpanSelection
from training_contracts.geracao import Citation
from training_lineage import LineageRepository, tables as t
from training_lineage.chat import ChatLineageRepository
from training_observability import chat_turn_span, manual_span, get_current_trace_id
from training_retrieval import RetrievalService, RetrievalScope
from .agents import GenerationError, RepairItem
from .validation import normalize_input, normalize_evidence, validate_items, sanitize_item

log=logging.getLogger(__name__)
_STAGE_PROMPTS={'E2':('input_judge',),'E3':('classifier',),'E4':('pedagogue',),
                'E8':('generation',),'E10':('output_judge','output_rubric')}


class ChatFailure(RuntimeError):
    """Categoria segura; nunca contém exceções/conteúdo do provedor ou storage."""


@dataclass
class _Execution:
    owner: str
    session: str
    prompt: str
    turn_id: str=field(default_factory=lambda:uuid4().hex)
    trace_id: str=''
    started: set=field(default_factory=set)
    durations: dict=field(default_factory=dict)
    input_verdict: object=None
    classification: object=None
    plan: object=None
    retrieval: object=None
    scope: dict=field(default_factory=dict)
    previous: object=None
    prior: list=field(default_factory=list)
    records: list=field(default_factory=list)
    citations: list=field(default_factory=list)
    verdicts: list=field(default_factory=list)
    provenance: dict=field(default_factory=dict)
    continuation: object=None
    repair_used: bool=False
    allocations: dict=field(default_factory=dict)
    errors: list=field(default_factory=list)
    e8_contexts: list=field(default_factory=list)
    latest_e8_call: str=''
    item_calls: dict=field(default_factory=dict)
    # Auditoria durável (0005): bruto de cada tentativa E8 por índice e prompt limitado.
    attempts: dict=field(default_factory=dict)
    calls: list=field(default_factory=list)
    stored_prompt: str=''
    input_audit: dict=field(default_factory=dict)
    # Fence de sessão: chat_turn_id da continuação lida no E1, mesmo se o estado for inválido.
    previous_head: object=None
    previous_invalid: bool=False
    more: bool=False


def _evidence_range(text,evidence):
    """Match normalizado com mapa de codepoints; ambiguidade falha, não escolhe página.

    Prefixos NFKC preservam composição/expansão de caracteres e espaços colapsados.
    O mapa serve só para localizar; a evidência persistida é a fatia original exata.
    """
    target=normalize_evidence(evidence)
    if not target: raise ValueError('evidência vazia')
    normalized='';positions=[]
    for end in range(1,len(text)+1):
        current=normalize_evidence(text[:end])
        shared=0
        while shared<min(len(current),len(normalized)) and current[shared]==normalized[shared]: shared+=1
        start=positions[shared][0] if shared<len(positions) else end-1
        positions=positions[:shared]+[(start,end)]*(len(current)-shared)
        normalized=current
    matches=[];offset=0
    while True:
        index=normalized.find(target,offset)
        if index<0: break
        after=index+len(target)
        if (index==0 or normalized[index-1]==' ') and (after==len(normalized) or normalized[after]==' '):
            start,end=positions[index][0],positions[after-1][1]
            if normalize_evidence(text[start:end])==target: matches.append((start,end))
        offset=index+1
    matches=list(dict.fromkeys(matches))
    if len(matches)!=1: raise ValueError('evidência ausente ou ambígua')
    return matches[0]


_EXECUTION_PROGRESS = ContextVar('training_chat_progress', default=None)


class _StageLatencies(SpanProcessor):
    """Um por TracerProvider (o SDK não remove processors); traces não coletados são limitados."""
    MAX_TRACES=256
    def __init__(self):
        from threading import Lock
        self.lock=Lock();self.traces={}
    @classmethod
    def attach(cls,provider):
        with _ATTACH_LOCK:
            existing=getattr(provider,'_training_stage_latencies',None)
            if existing is None:
                existing=cls();provider.add_span_processor(existing)
                provider._training_stage_latencies=existing
            return existing
    def on_start(self,span,parent_context=None): pass
    def on_end(self,span):
        if span.name not in {'E5_retrieval','E6_rerank','E7_context'}: return
        callback = _EXECUTION_PROGRESS.get()
        seconds = (span.end_time-span.start_time)/1e9
        if callback is not None:
            try: callback({'stage': span.name.split('_', 1)[0], 'seconds': seconds})
            except Exception: log.warning('Falha de telemetria de progresso; resposta preservada')
        trace_id=f'{span.context.trace_id:032x}'
        with self.lock:
            values=self.traces.setdefault(trace_id,{})
            values[span.name]=values.get(span.name,0)+(span.end_time-span.start_time)/1e9
            while len(self.traces)>self.MAX_TRACES: self.traces.pop(next(iter(self.traces)))
    def take(self,trace_id):
        with self.lock: return self.traces.pop(trace_id,{})
    def shutdown(self): pass
    def force_flush(self,timeout_millis=30000): return True


_ATTACH_LOCK=__import__('threading').Lock()
_TRACING_NOTICE=[False]


class ChatPipeline:
    def __init__(self,engine,generation,index,model_client,reranker,*,rate_limit=20,
                 rate_window_seconds=60,tracer_provider=None,progress=None,admission=None,evaluation_capture=False,stage_progress=None):
        if type(rate_limit) is not int or rate_limit<1 or rate_window_seconds<=0:
            raise ValueError('Configuração de limite inválida')
        self.engine,self.generation,self.index=engine,generation,index
        self.model,self.reranker=model_client,reranker
        self.rate_limit,self.rate_window_seconds=rate_limit,rate_window_seconds
        self.provider=tracer_provider or getattr(generation,'_tracer_provider',None) or TracerProvider()
        self.telemetry=_StageLatencies.attach(self.provider)
        self.progress=progress
        self.stage_progress=stage_progress
        self.admission=admission
        self.evaluation_capture=evaluation_capture
        self.last_evaluation={}
        self.last_execution={}
        # Provedor local OTel gera IDs verdadeiros; nenhum envio/publicação remota é simulado.
        if not _TRACING_NOTICE[0]:
            _TRACING_NOTICE[0]=True
            log.info('Chat usa tracing OTel local; exportação remota não configurada por esta pipeline')

    @contextmanager
    def _span(self,state,stage):
        start=perf_counter()
        with manual_span(stage,tracer_provider=self.provider,metadata={'etapa':stage}) as span:
            try:
                yield
            except GenerationError as exc:
                state.errors.append({'stage':stage,'kind':exc.kind,'status_code':exc.status_code,'provider_code':exc.provider_code})
                span.set_attribute('training.error.kind',exc.kind)
                if exc.status_code: span.set_attribute('training.error.http_status',exc.status_code)
                raise
            except asyncio.CancelledError:
                span.set_attribute('training.cancelled',True)
                raise
            except Exception as exc:
                state.errors.append({'stage':stage,'kind':type(exc).__name__})
                span.set_attribute('training.error.kind','execution')
                raise ChatFailure(stage+' falhou') from None
            finally:
                elapsed=perf_counter()-start
                state.durations[stage]=state.durations.get(stage,0)+elapsed
                if self.progress:
                    try: self.progress({'stage':stage,'seconds':elapsed})
                    except Exception: log.warning('Falha de telemetria de progresso; resposta preservada')

    async def _call(self,state,stage,operation,*args,_repair=False,_repairs_call=None,**kwargs):
        if self.admission is not None: await asyncio.to_thread(self.admission.acquire)
        state.started.add(stage)
        capture=None;audit=None
        if stage=='E8':
            state.latest_e8_call=uuid4().hex
            # Auditoria durável por chamada (0005), mesma semântica inicial/reparo da captura T-022.
            audit={'call_id':state.latest_e8_call,'kind':'reparo' if _repair else 'inicial','status':'completed',
                'failure_kind':None,'repairs_call_id':_repairs_call,'context':self._context_summary(args[3])}
            state.calls.append(audit)
        if stage=='E8' and self.evaluation_capture:
            global_refs={c.chunk_id:c.ref for c in state.retrieval.context.chunks}
            capture={'call_id':state.latest_e8_call,'call':'reparo' if _repair else 'inicial',
                'context':args[3].model_dump(mode='json'),
                'context_ref_mapping':{c.ref:global_refs[c.chunk_id] for c in args[3].chunks},'status':'started'}
            state.e8_contexts.append(capture)
        with self._span(state,stage):
            try:
                result=await operation(*args,**kwargs)
                if capture is not None: capture['status']='completed'
                return result
            except BaseException as exc:
                if capture is not None: capture['status']='failed'
                if audit is not None:
                    audit['status']='failed'
                    audit['failure_kind']=exc.kind if isinstance(exc,GenerationError) else 'execution'
                raise

    def _turn(self,state,response):
        meta=self.generation.metadata()
        models={k:v for k,v in meta.models.items() if k in state.started}
        names={name for stage in state.started for name in _STAGE_PROMPTS.get(stage,())}
        prompts={k:v for k,v in meta.prompt_versions.items() if k in names}
        classification=state.classification
        return ChatTurnRecord(chat_turn_id=state.turn_id,session_id=state.session,user_id=state.owner,
            prompt=state.stored_prompt,status=response.status,motivo_recusa=response.motivo_recusa,
            intencao=classification.intencao if classification else None,modo=classification.modo if classification else None,
            input_verdict=state.input_verdict,models=models,prompt_versions=prompts,
            trace_id=state.trace_id,created_at=datetime.now(timezone.utc))

    def _persist(self,state,response,*,failure=False):
        with self._span(state,'E12'):
            with self.engine.begin() as connection:
                repo=ChatLineageRepository(connection)
                if state.continuation is not None and not failure:
                    # Optimistic session fence: nenhuma transação fica aberta durante LLM.
                    if repo.get_continuation_head(state.owner,state.session)!=state.previous_head:
                        raise ChatFailure('Sessão avançou durante a geração')
                plan=SearchPlanRecord(**state.plan.model_dump(),search_plan_id=uuid4().hex,
                    chat_turn_id=state.turn_id) if state.plan and not failure else None
                repo.save_turn(self._turn(state,response),plan,
                    state.retrieval.run if state.retrieval and not failure else None,
                    state.records if not failure else [],state.citations if not failure else [],
                    state.verdicts if not failure else [],response,state.provenance if not failure else None,
                    input_audit=state.input_audit,calls=state.calls if not failure else (),
                    attempts=self._attempt_rows(state) if not failure else ())
                if state.continuation is not None and not failure:
                    # Só gerar_mais continua a progressão persistida; pedido novo abre a sua.
                    repo.save_continuation(state.owner,state.session,chat_turn_id=state.turn_id,
                        scope=state.scope,cursor=state.continuation,
                        used_chunk_ids=state.continuation['used_chunk_ids'],
                        quantity=(state.previous['quantity'] if state.more else 0)+len(response.itens),
                        new_progression=not state.more)
        return response

    def _attempt_rows(self,state):
        ids={index:record.item_id for index,record in enumerate(state.records)}
        return [dict(entry,item_id=ids[index]) for index,entries in state.attempts.items() if index in ids for entry in entries]

    async def run(self,owner_id,session_id,message,*,document_version_ids,section_prefix=(),count=5):
        # ContextVar é copiada por asyncio.to_thread, isolada por execução e
        # restaurada mesmo no cancelamento; nenhum callback global por provider.
        token = _EXECUTION_PROGRESS.set(self.stage_progress)
        try:
            return await self._run(owner_id,session_id,message,document_version_ids=document_version_ids,
                                   section_prefix=section_prefix,count=count)
        finally:
            _EXECUTION_PROGRESS.reset(token)

    async def _run(self,owner_id,session_id,message,*,document_version_ids,section_prefix=(),count=5):
        for value in (owner_id,session_id):
            if not isinstance(value,str) or not value.strip(): raise ValueError('Identidade e sessão explícitas obrigatórias')
        if not isinstance(message,str): raise ValueError('Mensagem deve ser texto')
        if type(count) is not int or not 1<=count<=self.generation.settings.max_items:
            raise ValueError('Quantidade fora do limite configurado')
        state=_Execution(owner_id,session_id,message)
        maximum=self.generation.settings.max_input_chars
        # S2: o turno guarda no máximo o limite configurado; tamanho e hash do bruto ficam na auditoria.
        state.stored_prompt=message[:maximum]
        state.input_audit={'raw_length':len(message),'stored_length':len(state.stored_prompt),
            'raw_sha256':sha256(message.encode('utf-8','surrogatepass')).hexdigest()}
        with chat_turn_span(state.turn_id,user_id=owner_id,session_id=session_id,tracer_provider=self.provider):
            state.trace_id=get_current_trace_id()
            if state.trace_id is None: raise ChatFailure('Tracing local indisponível')
            try:
                response=await self._execute(state,document_version_ids,section_prefix,count)
                response=self._persist(state,response)
            except asyncio.CancelledError:
                response=ChatResponse(status='erro',mensagem='Turno cancelado.',chat_turn_id=state.turn_id,trace_id=state.trace_id)
                try: self._persist(state,response,failure=True)
                except Exception: log.warning('Não foi possível persistir cancelamento de chat')
                raise
            except Exception as exc:
                if not isinstance(exc,GenerationError): state.errors.append({'stage':'chat','kind':type(exc).__name__})
                code=exc.kind if isinstance(exc,GenerationError) else 'execution'
                status=exc.status_code if isinstance(exc,GenerationError) else None
                response=ChatResponse(status='erro',mensagem='Não foi possível concluir o turno.',
                    chat_turn_id=state.turn_id,trace_id=state.trace_id)
                log.warning('Falha de chat categoria=%s http_status=%s',code,status)
                # Erro de commit não pode deixar passar a resposta educacional pendente.
                state.records=[];state.citations=[];state.verdicts=[];state.provenance={}
                if state.input_verdict is not None and state.input_verdict.malicioso: state.input_verdict=None
                try: self._persist(state,response,failure=True)
                except Exception: log.warning('Não foi possível persistir erro de chat; nenhuma entrega de itens')
            state.durations.update(self.telemetry.take(state.trace_id))
            self.last_execution={'chat_turn_id':state.turn_id,'trace_id':state.trace_id,
                'durations_seconds':state.durations,'models':self._turn(state,response).models,
                'prompt_versions':self._turn(state,response).prompt_versions,'status':response.status.value,'errors':state.errors}
            if self.evaluation_capture:
                self.last_evaluation={'chat_turn_id':state.turn_id,'contexts':state.e8_contexts,
                    'original_items':[{'item_id':r.item_id,'content':r.content.model_dump(mode='json'),**state.item_calls[index]} for index,r in enumerate(state.records) if r.status.value=='entregue']}
            return response

    async def _execute(self,state,versions,prefix,count):
        def response(status,message,**kwargs):
            return ChatResponse(status=status,mensagem=message,chat_turn_id=state.turn_id,trace_id=state.trace_id,**kwargs)
        with self._span(state,'E1'):
            maximum=self.generation.settings.max_input_chars
            normalized=normalize_input(state.prompt,max_chars=maximum)
            if len(state.prompt)>maximum or normalized.motivo_recusa is not None:
                return response('recusa','Mensagem excede o limite.',motivo_recusa=MotivoRecusa.TAMANHO_EXCEDIDO)
            if not normalized.text.strip(): return response('erro','Mensagem sem conteúdo após normalização.')
            prompt=normalized.text
            with self.engine.begin() as c:
                repo=ChatLineageRepository(c)
                slot=repo.acquire_chat_rate_slot(state.owner,self.rate_limit,self.rate_window_seconds)
            if not slot: return response('recusa','Limite de mensagens atingido.',motivo_recusa=MotivoRecusa.LIMITE_DE_TAXA)
            with self.engine.begin() as c:
                repo=ChatLineageRepository(c)
                state.scope=repo.canonicalize_scope(state.owner,dict(document_version_ids=list(versions),section_prefix=list(prefix)))
                try: state.previous=repo.get_continuation(state.owner,state.session)
                except (ValueError,PermissionError):
                    # Estado inválido nunca é herdado: gerar_mais falha; pedido novo o substitui.
                    state.previous=None;state.previous_invalid=True
                state.previous_head=repo.get_continuation_head(state.owner,state.session)
        judge=asyncio.create_task(self._call(state,'E2',self.generation.input_verdict,prompt))
        classifier=asyncio.create_task(self._call(state,'E3',self.generation.classify,prompt))
        try:
            try: state.input_verdict=await judge
            except Exception:
                return response('recusa','Não foi possível verificar a entrada.',motivo_recusa=MotivoRecusa.FALHA_DO_JUIZ_DE_ENTRADA)
            if state.input_verdict.malicioso:
                return response('recusa','Entrada recusada pela verificação de segurança.',motivo_recusa=MotivoRecusa.PROMPT_MALICIOSO)
            state.classification=Classification.model_validate((await classifier).model_dump())
        finally:
            for task in (judge,classifier):
                if not task.done(): task.cancel()
            await asyncio.gather(judge,classifier,return_exceptions=True)
        classification=state.classification
        if classification.intencao==Intent.FORA_DE_ESCOPO:
            return response('fora_de_escopo','Pedido fora do escopo de geração educacional.')
        more=state.more=classification.intencao==Intent.GERAR_MAIS
        if more:
            # Anti-repetição da sessão só vale para continuar a progressão (gerar_mais).
            with self.engine.connect() as c:
                history=c.execute(select(t.chat_item.c.record).select_from(t.chat_item.join(t.chat_turn)).where(
                    t.chat_turn.c.owner_id==state.owner,t.chat_turn.c.session_id==state.session)
                    .order_by(t.chat_turn.c.record['created_at'].astext,t.chat_item.c.item_id)).scalars().all()
                state.prior=[validar_item(record['content']) for record in history if record['status']=='entregue']
                if state.previous_invalid or not state.previous or state.previous['scope']!=state.scope or not state.prior:
                    raise ChatFailure('Continuação ausente, inválida ou fora do escopo')
                previous_turn=c.execute(select(t.chat_turn.c.record).where(
                    t.chat_turn.c.chat_turn_id==state.previous['chat_turn_id'],t.chat_turn.c.owner_id==state.owner,
                    t.chat_turn.c.session_id==state.session)).scalar_one()
                previous_plan=c.execute(select(t.chat_plan.c.record).where(
                    t.chat_plan.c.chat_turn_id==state.previous['chat_turn_id'])).scalar_one()
                state.classification=classification=Classification(intencao=Intent.GERAR_MAIS,modo=previous_turn['modo'])
                state.plan=SearchPlan.model_validate({k:v for k,v in previous_plan.items() if k not in ('search_plan_id','chat_turn_id')})
        if not more: state.plan=await self._call(state,'E4',self.generation.plan,prompt,classification)
        item_type=state.prior[-1].tipo if more else ('flashcard' if classification.intencao==Intent.GERAR_FLASHCARDS else 'question')
        # Cursor só em gerar_mais: o binding (owner/escopo/modo) é o do turno anterior, cujo modo foi herdado.
        cursor=state.previous['cursor'] if more else None
        scope=RetrievalScope(tuple(state.scope['document_version_ids']),tuple(state.scope['section_prefix']),cursor)
        retriever=RetrievalService(self.engine,self.index,self.model,self.reranker,chat_turn_id=state.turn_id,tracer_provider=self.provider)
        with self._span(state,'E5_E7'):
            state.retrieval=await asyncio.to_thread(retriever.retrieve,state.owner,state.plan,classification,scope,count)
        state.continuation=state.retrieval.continuation
        if state.retrieval.context is None: return response('conteudo_insuficiente','Não há contexto autorizado suficiente para gerar mais itens.')
        return await self._generate(state,prompt,classification,item_type,count,response)

    def _context_for(self,state,chunk_ids,allocation=None,*,allocations=None):
        # `allocations`: união filtrada de vários grupos (reparo único/E10), um chunk por entrada.
        allowed=[allocation] if allocations is None else list(allocations)
        chunks=[]
        with self.engine.connect() as c:
            repo=LineageRepository(c)
            maps=repo.load_chunk_provenance(state.owner,chunk_ids)
            for chunk in state.retrieval.context.chunks:
                if chunk.chunk_id not in chunk_ids: continue
                actual=repo.get_chunk(chunk.chunk_id);mapping=maps[chunk.chunk_id]
                spans=repo.list_canonical_spans(state.owner,mapping.canonical_id)
                ranges=[]
                for span in spans:
                    if span.kind=='separator': continue
                    if None not in allowed and not any(span.canonical_id==a.canonical_id and span.h1_id==a.h1_id and span.section_path==a.section_path for a in allowed): continue
                    prefix=tuple(state.scope['section_prefix'])
                    if span.section_path[:len(prefix)]!=prefix: continue
                    start=max(span.start,mapping.start);end=min(span.end,mapping.end)
                    if start<end: ranges.append((start,end))
                value='\n\n'.join(actual.text[start-mapping.start:end-mapping.start] for start,end in ranges)
                if value.strip(): chunks.append(chunk.model_copy(update={'ref':f'C{len(chunks)+1}','text':value}))
        if not chunks: raise ChatFailure('Alocação sem interseção canônica')
        return NumberedContext(chunks=chunks)

    def _resolve(self,state,item,allocation=None):
        citations=[];provenance={};content=[];sources={}
        context=state.retrieval.context
        with self.engine.connect() as connection:
            repo=LineageRepository(connection)
            for citation in item.citations:
                chunk=context.chunk(citation.chunk_ref)
                if chunk is None: raise ValueError('referência inexistente')
                actual=repo.get_chunk(chunk.chunk_id)
                start,end=_evidence_range(actual.text,citation.evidence)
                mapping=repo.load_chunk_provenance(state.owner,[actual.chunk_id])[actual.chunk_id]
                selection=CitationSpanSelection(chunk_id=actual.chunk_id,canonical_id=mapping.canonical_id,
                    start=mapping.start+start,end=mapping.start+end,evidence=actual.text[start:end])
                spans=repo.resolve_citation(state.owner,selection)
                exact={s.span_id:s for s in repo.list_canonical_spans(state.owner,mapping.canonical_id)}
                relevant=[exact[s.span_id] for s in spans if exact[s.span_id].kind!='separator']
                prefix=tuple(state.scope['section_prefix'])
                if not relevant or any(s.section_path[:len(prefix)]!=prefix for s in relevant):
                    raise ValueError('Citação fora da seção solicitada')
                if allocation is not None and any(s.canonical_id!=allocation.canonical_id or s.h1_id!=allocation.h1_id or s.section_path!=allocation.section_path for s in relevant):
                    raise ValueError('Citação fora da alocação canônica')
                cid=uuid4().hex
                content.append(Citation(chunk_ref=citation.chunk_ref,evidence=selection.evidence))
                citations.append((cid,citation.chunk_ref,actual.chunk_id,selection.evidence))
                provenance[cid]=selection
                version=repo.get_document_version(actual.document_version_id)
                pages={s.page for s in spans};sections={s.section_path for s in spans}
                sources[citation.chunk_ref]=Source(chunk_ref=citation.chunk_ref,chunk_id=actual.chunk_id,
                    document_id=version.document_id,document_version_id=version.document_version_id,
                    page=next(iter(pages)) if len(pages)==1 and all(s.provenance=='exact' for s in spans) else None,
                    section_path=list(next(iter(sections))) if len(sections)==1 else [])
        item=validar_item(item.model_dump(mode='json')|{'citations':[c.model_dump() for c in content]})
        return item,citations,provenance,sources

    @staticmethod
    def _context_summary(local):
        # Resumo durável do contexto exato de uma chamada; o texto continua só na fonte canônica.
        return {'chunks':[{'ref':c.ref,'chunk_id':c.chunk_id,'text_sha256':sha256(c.text.encode('utf-8','surrogatepass')).hexdigest(),
            'text_length':len(c.text)} for c in local.chunks]}

    async def _generate(self,state,prompt,classification,item_type,count,response):
        context=state.retrieval.context
        global_refs={c.chunk_id:c.ref for c in context.chunks}
        # Ref válida pelo schema e garantidamente ausente do contexto global: E9 rejeita como citacao_inexistente.
        missing_ref=f'C{len(context.chunks)+1}'
        groups=[(self._context_for(state,[c.chunk_id for c in context.chunks]),count,None)]
        if classification.modo.value=='cobertura':
            groups=[]
            for allocation in state.retrieval.allocations:
                if allocation.count<=0 or not allocation.chunk_ids: continue
                local=self._context_for(state,allocation.chunk_ids,allocation)
                groups.append((local,allocation.count,allocation))
        group_refs=[{c.ref:global_refs[c.chunk_id] for c in local.chunks} for local,_,_ in groups]
        def to_global(original,group):
            # Ref local desconhecida falha fechada; nunca é reinterpretada na numeração global.
            original=dict(original)
            if isinstance(original.get('citations'),list):
                original['citations']=[(c|{'chunk_ref':group_refs[group].get(c.get('chunk_ref'),missing_ref)}) if isinstance(c,dict) else c
                    for c in original['citations']]
            return original
        group_chunks=[{c.chunk_id for c in local.chunks} for local,_,_ in groups]
        attempt_of={}
        def remember(index,attempt,local_index,original):
            # attempt=2 ⇔ a chamada que produziu o item é de reparo (envelope ou item), como na captura T-022.
            attempt_of[index]=attempt
            state.item_calls[index]={'call_id':state.latest_e8_call,'generation_item_index':local_index,'attempt':attempt}
            state.attempts.setdefault(index,[]).append({'attempt':attempt,'call_id':state.latest_e8_call,
                'generation_item_index':local_index,'raw_content':dict(original)})
        def combined(group_ids):
            # Contexto filtrado só dos grupos envolvidos, um chunk por entrada (mapeamento injetivo).
            group_ids=sorted(group_ids)
            if len(group_ids)==1: local=groups[group_ids[0]][0]
            else:
                chunk_ids=list(dict.fromkeys(c.chunk_id for g in group_ids for c in groups[g][0].chunks))
                local=self._context_for(state,chunk_ids,allocations=[groups[g][2] for g in group_ids])
            return local,{c.chunk_id:c.ref for c in local.chunks}
        def global_chunk(ref):
            chunk=context.chunk(ref) if isinstance(ref,str) else None
            return chunk.chunk_id if chunk is not None else None
        raw=[];raw_local=[];group_of={};envelope_omissions=[]
        for group,(local,amount,allocation) in enumerate(groups):
            try:
                result=await self._call(state,'E8',self.generation.generate,prompt,classification,state.plan,local,amount,item_type=item_type)
            except GenerationError as exc:
                if exc.kind!='invalid_json': raise
                if state.repair_used:
                    envelope_omissions.append(Omission(motivo=MotivoRejeicao.VIOLACAO_DE_SCHEMA,detalhe='Envelope inválido; reparo já utilizado.'));continue
                state.repair_used=True;failed_call=state.latest_e8_call
                try: result=await self._call(state,'E8',self.generation.generate,prompt,classification,state.plan,local,amount,item_type=item_type,_repair=True,_repairs_call=failed_call)
                except GenerationError as second:
                    if second.kind!='invalid_json': raise
                    envelope_omissions.append(Omission(motivo=MotivoRejeicao.VIOLACAO_DE_SCHEMA,detalhe='Envelope inválido após um reparo.'));continue
            repaired_envelope=state.calls[-1]['repairs_call_id'] is not None
            for local_index,original in enumerate(result.itens):
                index=len(raw)
                # Itens do reparo de envelope são a tentativa 2 (ligada à chamada inicial falha); nenhum bruto inicial é inventado.
                remember(index,2 if repaired_envelope else 1,local_index,original)
                state.allocations[index]=allocation;group_of[index]=group
                raw_local.append(dict(original));raw.append(to_global(original,group))
        ids=[uuid4().hex for _ in raw];final={};resolved={};reasons={};valid_content={}
        prior_session=state.prior if state.more else []
        def judge_ref(index,ref,by_chunk):
            chunk_id=global_chunk(ref)
            return by_chunk.get(chunk_id) if chunk_id in group_chunks[group_of[index]] else None
        async def assess(batch,indices):
            with self._span(state,'E9'):
                checks=validate_items(RawGenerationOutput(itens=batch),context,expected_type=item_type,
                    prior=[*prior_session,*[item for i,item in final.items() if i not in indices]])
                accepted={}
                for local_index,(original,index) in enumerate(zip(batch,indices)):
                    try: valid_content[index]=validar_item(original)
                    except Exception: pass
                    verdict=checks.verdicts[local_index]
                    item=checks.accepted.get(local_index)
                    if item is not None:
                        try:
                            record=self._resolve(state,item,state.allocations.get(index))
                            item=record[0];resolved[index]=record;valid_content[index]=item
                        except (ValueError,KeyError,PermissionError):
                            verdict=verdict.model_copy(update={'aprovado':False,'motivo':MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA,'detalhe':'Evidência sem mapeamento canônico inequívoco.'})
                            item=None
                    state.verdicts.append(VerdictRecord(verdict_id=uuid4().hex,item_id=ids[index],origem='E9',
                        tentativa=attempt_of[index],aprovado=verdict.aprovado,motivo=verdict.motivo,detalhe=verdict.detalhe,created_at=datetime.now(timezone.utc)))
                    if item is not None: accepted[index]=item
                    else: reasons.setdefault(index,(verdict.motivo,verdict.detalhe))
            if accepted:
                indexes=list(accepted)
                judged_context,by_chunk=combined({group_of[i] for i in indexes})
                for index in list(indexes):
                    # Defesa em profundidade: citação fora do contexto filtrado não vai ao juiz nem é entregue.
                    if any(judge_ref(index,c.chunk_ref,by_chunk) is None for c in accepted[index].citations):
                        indexes.remove(index);accepted.pop(index)
                        reasons.setdefault(index,(MotivoRejeicao.EVIDENCIA_NAO_ENCONTRADA,'Citação fora do contexto filtrado.'))
            if accepted:
                judged_context,by_chunk=combined({group_of[i] for i in indexes})
                judged=[validar_item(accepted[i].model_dump(mode='json')|{'citations':[c.model_dump()|{'chunk_ref':judge_ref(i,c.chunk_ref,by_chunk)}
                    for c in accepted[i].citations]}) for i in indexes]
                result=await self._call(state,'E10',self.generation.judge,judged,judged_context)
                if {v.item_index for v in result.vereditos}!=set(range(len(indexes))): raise ChatFailure('Juiz incompleto')
                for v in result.vereditos:
                    index=indexes[v.item_index]
                    state.verdicts.append(VerdictRecord(verdict_id=uuid4().hex,item_id=ids[index],origem='E10',
                        tentativa=attempt_of[index],aprovado=v.aprovado,motivo=None if v.aprovado else MotivoRejeicao.REPROVADO_NO_JUIZ_DE_SAIDA,
                        detalhe=None if v.aprovado else v.motivo,fundamentado=v.fundamentado,conforme_rubrica=v.conforme_rubrica,
                        judge_model=self.generation.metadata().models['E10'],created_at=datetime.now(timezone.utc)))
                    if v.aprovado: final[index]=accepted[index]
                    else: reasons.setdefault(index,(MotivoRejeicao.REPROVADO_NO_JUIZ_DE_SAIDA,v.motivo))
        await assess(raw,list(range(len(raw))))
        failed=[i for i in range(len(raw)) if i not in final]
        if failed and not state.repair_used:
            # Orçamento global único (RAG-JUI-003 e T-021): UMA chamada E8 de reparo por turno, inclusive
            # na cobertura com vários grupos; recebe só o contexto filtrado dos grupos reprovados.
            state.repair_used=True
            local,by_chunk=combined({group_of[i] for i in failed})
            refs_local={c.ref:c.chunk_id for c in local.chunks}
            def to_repair(index):
                original=dict(raw_local[index])
                if isinstance(original.get('citations'),list):
                    original['citations']=[(c|{'chunk_ref':by_chunk.get(global_chunk(group_refs[group_of[index]].get(c.get('chunk_ref'))),c.get('chunk_ref'))})
                        if isinstance(c,dict) else c for c in original['citations']]
                return original
            def from_repair(index,original):
                # Ref fora do contexto do reparo ou de outro grupo falha fechada como citacao_inexistente.
                original=dict(original)
                if isinstance(original.get('citations'),list):
                    def mapped(ref):
                        chunk_id=refs_local.get(ref)
                        return global_refs[chunk_id] if chunk_id in group_chunks[group_of[index]] else missing_ref
                    original['citations']=[(c|{'chunk_ref':mapped(c.get('chunk_ref'))}) if isinstance(c,dict) else c for c in original['citations']]
                return original
            repairs=[RepairItem(to_repair(i),*reasons[i]) for i in failed]
            try:
                fixed=await self._call(state,'E8',self.generation.generate,prompt,classification,state.plan,local,len(failed),repairs,item_type=item_type,_repair=True)
            except GenerationError as exc:
                if exc.kind!='invalid_json': raise
                fixed=None
            if fixed is not None:
                repaired=failed[:len(fixed.itens)]
                for local_index,index in enumerate(repaired):
                    original=fixed.itens[local_index]
                    remember(index,2,local_index,original)
                    raw_local[index]=dict(original);raw[index]=from_repair(index,original)
                if repaired: await assess([raw[i] for i in repaired],repaired)
        delivered=[];omissions=list(envelope_omissions);source_by_ref={}
        with self._span(state,'E11'):
            for index,original in enumerate(raw):
                item=final.get(index)
                if item is not None:
                    safe=sanitize_item(item)
                    delivered.append(DeliveredItem(item_id=ids[index],conteudo=safe.delivered))
                else:
                    reason,detail=reasons[index]
                    omissions.append(Omission(item_id=ids[index],motivo=reason,detalhe=detail))
                content=item or valid_content.get(index)
                # raw_content é o bruto da última tentativa (refs globais); cada tentativa fica em chat_generation_attempt.
                state.records.append(ItemRecord(item_id=ids[index],chat_turn_id=state.turn_id,
                    raw_content=original,content=content,status='entregue' if item else 'omitido',motivo=None if item else reasons[index][0]))
                if index in resolved and content is not None:
                    canonical,citations,provenance,sources=resolved[index]
                    # Só fontes realmente presentes no content atual; nunca fabricar para inválidos.
                    if canonical.model_dump()==content.model_dump():
                        state.citations.extend(CitationRecord(citation_id=cid,item_id=ids[index],chunk_ref=ref,chunk_id=chunk,evidence=evidence)
                            for cid,ref,chunk,evidence in citations)
                        state.provenance.update(provenance)
                        if item:
                            for ref,source in sources.items():
                                previous=source_by_ref.get(ref)
                                if previous and previous!=source:
                                    source=source.model_copy(update={'page':None,'section_path':source.section_path if previous.section_path==source.section_path else []})
                                source_by_ref[ref]=source
        return response('ok',f'{len(delivered)} item(ns) gerado(s).',itens=delivered,omissoes=omissions,fontes=list(source_by_ref.values()))


class RequestAdmission:
    """Admissão local de chamadas externas; espaça inícios e nunca retenta falhas."""
    def __init__(self,interval_seconds=6,call_budget=12):
        from threading import Lock
        import math
        if not math.isfinite(interval_seconds) or interval_seconds<0 or type(call_budget) is not int or call_budget<1:
            raise ValueError('Orçamento de chamadas inválido')
        self.interval=interval_seconds;self.budget=call_budget;self.calls=0;self.next=0.;self.lock=Lock()
    def acquire(self):
        from time import monotonic,sleep
        with self.lock:
            if self.calls>=self.budget: raise ChatFailure('Orçamento de chamadas esgotado')
            now=monotonic();admitted=max(now,self.next)
            self.next=admitted+self.interval;self.calls+=1
        delay=admitted-now
        if delay>0: sleep(delay)


class AdmittedQueries:
    """Embeddings reais do cliente existente sob o mesmo orçamento do chat."""
    def __init__(self,model,admission): self.model,self.admission=model,admission
    def embed_query(self,query):
        self.admission.acquire()
        return self.model.embed_query(query)
