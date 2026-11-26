"""E12 PostgreSQL, sem commit: o chamador só entrega após confirmar sua transação.

save_turn exige SearchPlanRecord (não SearchPlan sem IDs), provenance mapeia
citation_id -> CitationSpanSelection T010. get_item_lineage retorna
{'lineage': ItemLineage, 'spans': {citation_id: tuple[ResolvedSpan, ...]}}.
Não infere a primeira ocorrência de uma evidência repetida.
"""
from collections import Counter
from uuid import uuid4

from sqlalchemy import select, text, func
from sqlalchemy.dialects.postgresql import insert
from training_contracts.chat import ChatResponse
from training_contracts.rendering import render_item
from training_contracts.ingestao import CitationSpanSelection, ResolvedSpan
from training_contracts.linhagem import (
    ChatTurnRecord, SearchPlanRecord, RetrievalRunRecord, ItemRecord,
    CitationRecord, VerdictRecord, ItemLineage, CitationLineage,
)
from training_lineage import tables as t
from training_lineage.repository import LineageRepository


def validated(cls, value):
    # Revalidar inclusive model_copy/model_construct, que não validam alterações.
    return cls.model_validate(value.model_dump(mode='json') if isinstance(value, cls) else value)


def dumped(value):
    return value.model_dump(mode='json')


def require_id(value):
    if not isinstance(value,str) or not value.strip():
        raise ValueError('Identificador obrigatório')


class ChatLineageRepository:
    def __init__(self, connection):
        self.connection = connection
        self.lineage = LineageRepository(connection)

    def _owned_chunks(self, owner_id, ids):
        ids=set(ids)
        if not ids:
            return
        found=set(self.connection.execute(select(t.chunk.c.chunk_id).select_from(
            t.chunk.join(t.document_version)).where(t.document_version.c.owner_id==owner_id,
            t.chunk.c.chunk_id.in_(ids))).scalars())
        if found != ids:
            raise PermissionError('Chunk ausente ou inacessível ao owner')

    def save_turn(self, turn, plan, run, items, citations, verdicts, response, provenance=None, *,
                  input_audit=None, calls=(), attempts=()):
        """Insere o grafo inteiro em savepoint; falha não deixa fragmentos.

        Nenhum commit/rollback da transação externa. IDs/trace da resposta são
        obrigatórios e precisam ser exatamente os do turno persistido.
        `input_audit` (0005): raw_length/raw_sha256/stored_length do prompt bruto;
        `calls` (0005): cada chamada E8 (inicial/reparo, completed/failed, categoria da
        falha, chamada falha reparada) com resumo do contexto (ref/chunk_id/hash/tamanho).
        `attempts` (0005): bruto de cada tentativa de item ligado à chamada concluída que o
        produziu; attempt=2 ⇔ chamada de reparo. Item sem tentativa 1 só é aceito se veio
        do reparo de um envelope inicial falho (nenhum bruto inicial é inventado).
        """
        turn=validated(ChatTurnRecord,turn)
        response=validated(ChatResponse,response)
        plan=validated(SearchPlanRecord,plan) if plan is not None else None
        run=validated(RetrievalRunRecord,run) if run is not None else None
        items=[validated(ItemRecord,i) for i in items]
        citations=[validated(CitationRecord,c) for c in citations]
        verdicts=[validated(VerdictRecord,v) for v in verdicts]
        provenance=provenance or {}
        if (response.chat_turn_id,response.trace_id,response.status,response.motivo_recusa)!=(
                turn.chat_turn_id,turn.trace_id,turn.status,turn.motivo_recusa):
            raise ValueError('Resposta diverge do turno/trace/status')
        for record in [plan,run,*items]:
            if record is not None and record.chat_turn_id!=turn.chat_turn_id:
                raise ValueError('Registro pertence a outro turno')
        if run is not None and plan is None:
            raise ValueError('Recuperação exige plano com IDs reais')
        if run is not None and run.modo!=turn.modo:
            raise ValueError('Modo de recuperação diverge do turno')
        if turn.motivo_recusa is not None and turn.motivo_recusa.value in ('limite_de_taxa','tamanho_excedido') and (plan or run or items or citations or verdicts):
            raise ValueError('E1 não executa etapas posteriores')
        item_map={i.item_id:i for i in items}
        if len(item_map)!=len(items) or len({c.citation_id for c in citations})!=len(citations) or len({v.verdict_id for v in verdicts})!=len(verdicts):
            raise ValueError('ID duplicado')
        if any(c.item_id not in item_map for c in citations) or any(v.item_id not in item_map for v in verdicts):
            raise ValueError('Citação/veredito de outro item')
        if set(provenance)!={c.citation_id for c in citations}:
            raise ValueError('Proveniência exata obrigatória por citação')
        delivered={i.item_id:dumped(render_item(i.content)) for i in items if i.status.value=='entregue'}
        if delivered!={i.item_id:dumped(i.conteudo) for i in response.itens}:
            raise ValueError('Itens entregues divergem da resposta')
        omitted={i.item_id:i.motivo for i in items if i.status.value=='omitido'}
        if omitted!={o.item_id:o.motivo for o in response.omissoes if o.item_id is not None}:
            raise ValueError('Omissões divergem dos registros')
        for i in items:
            actual=Counter((c.chunk_ref,c.evidence) for c in citations if c.item_id==i.item_id)
            expected=Counter((c.chunk_ref,c.evidence) for c in i.content.citations) if i.content is not None else Counter()
            if actual-expected or (i.status.value=='entregue' and actual!=expected):
                raise ValueError('Citações divergem do conteúdo')
            # Omitidos podem ter citações inválidas no content/raw original;
            # não fabricar CitationRecord/proveniência para uma fonte inexistente.
            if i.status.value=='entregue':
                latest=[v for v in verdicts if v.item_id==i.item_id]
                for origin in ('E9','E10'):
                    stage=[v for v in latest if v.origem.value==origin]
                    if not stage or not max(stage,key=lambda v:v.tentativa).aprovado:
                        raise ValueError('Entrega exige aprovação E9/E10')
        if len({(v.item_id,v.origem,v.tentativa) for v in verdicts})!=len(verdicts):
            raise ValueError('Veredito duplicado por etapa/tentativa')
        candidate_ids=[c.chunk_id for c in run.candidates] if run else []
        if len(set(candidate_ids))!=len(candidate_ids):
            raise ValueError('Candidato duplicado')
        selected=set(run.selected) if run else set()
        if any(c.chunk_id not in selected for c in citations):
            raise ValueError('Citação fora do contexto selecionado')
        audit=self._validated_input_audit(turn,input_audit)
        calls=self._validated_calls(run,calls)
        attempts=self._validated_attempts(item_map,calls,attempts)
        resolved={}
        citation_sources={}
        with self.connection.begin_nested():
            self._owned_chunks(turn.user_id,candidate_ids)
            # A barreira trava reservas antes dos chunks; não pré-travar chunks
            # aqui, pois escritores fenced usam a ordem reserva -> chunk.
            authorized=self.lineage.authorize_candidates(turn.user_id,selected)
            if set(authorized)!=selected:
                raise PermissionError('Contexto revogado, indisponível ou não publicado')
            for c in citations:
                selection=validated(CitationSpanSelection,provenance[c.citation_id])
                if (selection.chunk_id,selection.evidence)!=(c.chunk_id,c.evidence):
                    raise ValueError('Seleção diverge da citação')
                spans=self.lineage.resolve_citation(turn.user_id,selection)
                resolved[c.citation_id]=(selection,spans)
                chunk=self.lineage.get_chunk(c.chunk_id)
                version=self.lineage.get_document_version(chunk.document_version_id)
                # Uma página só é exata se todos os spans concordam, sem unknown.
                pages={s.page for s in spans}
                page=next(iter(pages)) if len(pages)==1 and all(s.provenance=='exact' for s in spans) else None
                sections={s.section_path for s in spans}
                section=list(next(iter(sections))) if len(sections)==1 else []
                source=dict(chunk_ref=c.chunk_ref,chunk_id=c.chunk_id,document_id=version.document_id,
                    document_version_id=version.document_version_id,page=page,section_path=section)
                if c.item_id in delivered:
                    previous=citation_sources.get(c.chunk_ref)
                    if previous is not None and previous!=source:
                        # Múltiplas seleções do mesmo chunk podem alcançar páginas distintas.
                        if previous['chunk_id']!=c.chunk_id:
                            raise ValueError('chunk_ref ambíguo')
                        source['page']=None
                        source['section_path']=previous['section_path'] if previous['section_path']==section else []
                    citation_sources[c.chunk_ref]=source
            if {s.chunk_ref:dumped(s) for s in response.fontes}!=citation_sources:
                raise ValueError('Fontes divergem da proveniência canônica exata')
            self.connection.execute(t.chat_turn.insert().values(chat_turn_id=turn.chat_turn_id,
                owner_id=turn.user_id,session_id=turn.session_id,trace_id=turn.trace_id,
                record=dumped(turn),response=dumped(response)))
            if plan:
                self.connection.execute(t.chat_plan.insert().values(search_plan_id=plan.search_plan_id,
                    chat_turn_id=turn.chat_turn_id,record=dumped(plan)))
            if run:
                self.connection.execute(t.chat_retrieval.insert().values(retrieval_run_id=run.retrieval_run_id,
                    chat_turn_id=turn.chat_turn_id,record=dumped(run)))
                for candidate in run.candidates:
                    self.connection.execute(t.chat_candidate.insert().values(chat_turn_id=turn.chat_turn_id,
                        chunk_id=candidate.chunk_id,owner_id=turn.user_id,selected=candidate.selected))
            for i in items:
                self.connection.execute(t.chat_item.insert().values(item_id=i.item_id,
                    chat_turn_id=turn.chat_turn_id,record=dumped(i)))
            for c in citations:
                selection,spans=resolved[c.citation_id]
                self.connection.execute(t.chat_citation.insert().values(citation_id=c.citation_id,
                    item_id=c.item_id,chat_turn_id=turn.chat_turn_id,owner_id=turn.user_id,
                    chunk_id=c.chunk_id,record=dumped(c),selection=dumped(selection),spans=[dumped(s) for s in spans]))
            for v in verdicts:
                self.connection.execute(t.chat_verdict.insert().values(verdict_id=v.verdict_id,
                    item_id=v.item_id,record=dumped(v)))
            if audit is not None:
                self.connection.execute(t.chat_input_audit.insert().values(chat_turn_id=turn.chat_turn_id,**audit))
            for call in calls:  # ordem da execução: a chamada reparada vem antes do reparo (FK)
                self.connection.execute(t.chat_generation_call.insert().values(chat_turn_id=turn.chat_turn_id,**call))
            for a in attempts:
                self.connection.execute(t.chat_generation_attempt.insert().values(chat_turn_id=turn.chat_turn_id,**a))

    @staticmethod
    def _validated_input_audit(turn, audit):
        if audit is None:
            return None
        import re
        if not isinstance(audit,dict) or set(audit)!={'raw_length','raw_sha256','stored_length'}:
            raise ValueError('Auditoria de entrada inválida')
        raw,digest,stored=audit['raw_length'],audit['raw_sha256'],audit['stored_length']
        if any(type(v) is not int or v<0 for v in (raw,stored)) or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Auditoria de entrada inválida')
        if stored!=len(turn.prompt) or stored>raw:
            raise ValueError('Auditoria de entrada diverge do prompt persistido')
        return dict(raw_length=raw,raw_sha256=digest,stored_length=stored)

    @staticmethod
    def _validated_calls(run, calls):
        import json
        selected=set(run.selected) if run else set()
        result=[];known={}
        for call in calls:
            if not isinstance(call,dict) or set(call)!={'call_id','kind','status','failure_kind','repairs_call_id','context'}:
                raise ValueError('Chamada de geração inválida')
            cid=call['call_id']
            if not isinstance(cid,str) or not cid.strip() or cid in known:
                raise ValueError('Chamada de geração repetida ou sem ID')
            if call['kind'] not in ('inicial','reparo') or call['status'] not in ('completed','failed'):
                raise ValueError('Chamada de geração inválida')
            if (call['status']=='failed')!=(isinstance(call['failure_kind'],str) and bool(call['failure_kind'].strip())):
                raise ValueError('Falha de chamada exige categoria, e só ela')
            repaired=call['repairs_call_id']
            if repaired is not None and (call['kind']!='reparo' or repaired not in known
                    or known[repaired]['kind']!='inicial' or known[repaired]['status']!='failed'):
                raise ValueError('Reparo de envelope deve apontar uma chamada inicial falha anterior')
            context=json.loads(json.dumps(call['context'],allow_nan=False))
            if not isinstance(context,dict) or set(context)!={'chunks'} or not isinstance(context['chunks'],list):
                raise ValueError('Contexto da chamada inválido')
            for chunk in context['chunks']:
                if not isinstance(chunk,dict) or set(chunk)!={'ref','chunk_id','text_sha256','text_length'} or chunk['chunk_id'] not in selected:
                    raise ValueError('Contexto da chamada fora do contexto selecionado')
            known[cid]=dict(call,context=context);result.append(known[cid])
        return result

    @staticmethod
    def _validated_attempts(item_map, calls, attempts):
        import json
        by_call={c['call_id']:c for c in calls}
        result=[];seen=set();slots=set();history={}
        for a in attempts:
            if not isinstance(a,dict) or set(a)!={'item_id','attempt','call_id','generation_item_index','raw_content'}:
                raise ValueError('Tentativa de geração inválida')
            key=(a['item_id'],a['attempt'])
            if a['item_id'] not in item_map or type(a['attempt']) is not int or a['attempt'] not in (1,2) or key in seen:
                raise ValueError('Tentativa de geração de outro item ou repetida')
            seen.add(key)
            call=by_call.get(a['call_id'])
            if call is None or call['status']!='completed':
                raise ValueError('Tentativa sem chamada E8 concluída')
            if (call['kind']=='reparo')!=(a['attempt']==2):
                raise ValueError('Tentativa incoerente com o tipo da chamada E8')
            if type(a['generation_item_index']) is not int or a['generation_item_index']<0 or (a['call_id'],a['generation_item_index']) in slots:
                raise ValueError('Índice de geração inválido ou repetido na chamada')
            slots.add((a['call_id'],a['generation_item_index']))
            raw=json.loads(json.dumps(a['raw_content'],allow_nan=False))
            if not isinstance(raw,dict):
                raise ValueError('Tentativa de geração inválida')
            history.setdefault(a['item_id'],{})[a['attempt']]=call
            result.append(dict(a,raw_content=raw))
        for item_id,entries in history.items():
            if 1 not in entries and entries[2]['repairs_call_id'] is None:
                raise ValueError('Tentativa de reparo sem tentativa inicial nem envelope inicial falho')
            if 1 in entries and 2 in entries and entries[2]['repairs_call_id'] is not None:
                raise ValueError('Item com tentativa inicial não vem de reparo de envelope')
        return result

    def get_generation_attempts(self, owner_id, item_id):
        """Tentativas E8 de um item do owner, em ordem, com a chamada que as produziu.

        Owner alheio recebe lista vazia. Para item vindo do reparo de envelope, `call`
        traz `repairs_call_id` e a chamada inicial falha aparece em `get_generation_calls`.
        """
        require_id(owner_id)
        a,c=t.chat_generation_attempt,t.chat_generation_call
        rows=self.connection.execute(select(a,c.c.kind,c.c.repairs_call_id,c.c.context).select_from(
            a.join(c,c.c.call_id==a.c.call_id).join(t.chat_turn,t.chat_turn.c.chat_turn_id==a.c.chat_turn_id)).where(
            a.c.item_id==item_id,t.chat_turn.c.owner_id==owner_id).order_by(a.c.attempt)).mappings().all()
        return [dict(item_id=r['item_id'],attempt=r['attempt'],call_id=r['call_id'],call=r['kind'],
            repairs_call_id=r['repairs_call_id'],generation_item_index=r['generation_item_index'],
            raw_content=r['raw_content'],context=r['context']) for r in rows]

    def get_generation_calls(self, owner_id, chat_turn_id):
        """Chamadas E8 do turno do owner, inclusive falhas de envelope; owner alheio recebe []."""
        require_id(owner_id)
        c=t.chat_generation_call
        rows=self.connection.execute(select(c).select_from(c.join(t.chat_turn,t.chat_turn.c.chat_turn_id==c.c.chat_turn_id)).where(
            c.c.chat_turn_id==chat_turn_id,t.chat_turn.c.owner_id==owner_id)).mappings().all()
        return [dict(r) for r in rows]

    def get_item_lineage(self, owner_id, item_id):
        row=self.connection.execute(select(t.chat_item.c.record,t.chat_turn.c.trace_id).select_from(
            t.chat_item.join(t.chat_turn)).where(t.chat_item.c.item_id==item_id,
            t.chat_turn.c.owner_id==owner_id)).mappings().first()
        if row is None:
            return None
        item=validated(ItemRecord,row['record'])
        if item.status.value!='entregue':
            return None
        citations=[]
        exact={}
        rows=self.connection.execute(select(t.chat_citation).where(t.chat_citation.c.item_id==item_id,
            t.chat_citation.c.owner_id==owner_id).order_by(t.chat_citation.c.citation_id)).mappings()
        for r in rows:
            c=validated(CitationRecord,r['record'])
            selection=validated(CitationSpanSelection,r['selection'])
            spans=self.lineage.resolve_citation(owner_id,selection)
            if [dumped(s) for s in spans]!=r['spans']:
                raise ValueError('Proveniência persistida diverge da fonte')
            exact[c.citation_id]=tuple(ResolvedSpan.model_validate(s) for s in spans)
            chunk=self.lineage.get_chunk(c.chunk_id)
            version=self.lineage.get_document_version(chunk.document_version_id)
            pages={s.page for s in spans}
            sections={s.section_path for s in spans}
            citations.append(CitationLineage(citation_id=c.citation_id,chunk_ref=c.chunk_ref,
                chunk_id=c.chunk_id,evidence=c.evidence,document_id=version.document_id,
                document_version_id=version.document_version_id,
                page=next(iter(pages)) if len(pages)==1 and all(s.provenance=='exact' for s in spans) else None,
                section_path=list(next(iter(sections))) if len(sections)==1 else []))
        return {'lineage':ItemLineage(item_id=item_id,chat_turn_id=item.chat_turn_id,
            trace_id=row['trace_id'],content=item.content,citations=citations),'spans':exact}

    def acquire_chat_rate_slot(self, owner_id, limit, window_seconds):
        """Janela deslizante; lock transacional por owner, relógio após o lock.

        O chamador deve confirmar o slot mesmo quando registrar erro/recusa;
        rollback libera a reserva. Use transação curta própria antes de chamar LLM.
        """
        require_id(owner_id)
        if isinstance(limit,bool) or not isinstance(limit,int) or limit<1 or isinstance(window_seconds,bool) or not isinstance(window_seconds,(int,float)) or not 0<window_seconds< float('inf'):
            raise ValueError('Limite/janela inválidos')
        self.connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                                {'key':'chat-rate:'+owner_id})
        now=self.connection.execute(select(func.clock_timestamp())).scalar_one()
        count=self.connection.execute(text('SELECT count(*) FROM chat_rate_event WHERE owner_id=:owner AND acquired_at > :now - :seconds * interval \'1 second\''),
            {'owner':owner_id,'now':now,'seconds':window_seconds}).scalar_one()
        if count>=limit:
            return False
        self.connection.execute(t.chat_rate_event.insert().values(event_id=uuid4().hex,owner_id=owner_id,acquired_at=now))
        return True

    def canonicalize_scope(self, owner_id, scope):
        """Escopo semântico para persistir e enviar ao RetrievalService.

        Só versões explícitas do owner, únicas/ordenadas, e prefixo de seção
        em ordem hierárquica. Omissão de seção significa lista vazia.
        """
        require_id(owner_id)
        if not isinstance(scope,dict):
            raise ValueError('Escopo deve ser objeto JSON')
        if set(scope)-{'document_version_ids','section_prefix'}:
            raise ValueError('Chave de escopo desconhecida')
        versions=scope.get('document_version_ids',[])
        prefix=scope.get('section_prefix',[])
        if not isinstance(versions,(list,tuple)) or not versions or any(not isinstance(v,str) or not v.strip() for v in versions):
            raise ValueError('Escopo exige versões explícitas')
        if not isinstance(prefix,(list,tuple)) or any(not isinstance(s,str) or not s.strip() for s in prefix):
            raise ValueError('Seção inválida')
        versions=sorted(set(versions))
        for version_id in versions:
            version=self.lineage.get_document_version(version_id)
            if version is None or version.owner_id!=owner_id:
                raise PermissionError('Escopo inacessível ao owner')
        return dict(document_version_ids=versions,section_prefix=list(prefix))

    def _canonical_cursor(self, owner_id, session_id, chat_turn_id, original_scope, scope, cursor, ids):
        if cursor is None:
            return None
        if not isinstance(cursor,dict):
            raise ValueError('Cursor deve ser objeto JSON')
        import json
        from hashlib import sha256
        # Copiar sem alterar o cursor do chamador e rejeitar JSON não finito.
        cursor=json.loads(json.dumps(cursor,allow_nan=False))
        if 'used_chunk_ids' in cursor:
            used=cursor['used_chunk_ids']
            if not isinstance(used,list) or any(not isinstance(i,str) or not i.strip() for i in used) or len(set(used))!=len(used) or set(used)!=set(ids):
                raise ValueError('Cursor diverge dos chunks usados')
            cursor['used_chunk_ids']=ids
        if 'binding' in cursor or 'version' in cursor:
            if set(cursor)!={'version','binding','used_chunk_ids','remaining'} or type(cursor['version']) is not int or cursor['version']!=1 or type(cursor['remaining']) is not int or cursor['remaining']<0:
                raise ValueError('Cursor de recuperação inválido')
            record=self.connection.execute(select(t.chat_turn.c.record).where(
                t.chat_turn.c.owner_id==owner_id,t.chat_turn.c.session_id==session_id,
                t.chat_turn.c.chat_turn_id==chat_turn_id)).scalar_one_or_none()
            if record is None or record.get('modo') not in ('consulta','cobertura'):
                raise ValueError('Cursor binding exige turno/modo do owner')
            def binding(versions,prefix):
                value=[owner_id,versions,prefix,record['modo']]
                return sha256(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            canonical=binding(scope['document_version_ids'],scope['section_prefix'])
            original=binding(original_scope['document_version_ids'],original_scope.get('section_prefix',[]))
            if cursor['binding'] not in (original,canonical):
                raise ValueError('Cursor binding diverge de owner/escopo/modo')
            cursor['binding']=canonical
        return cursor

    def save_continuation(self, owner_id, session_id, *, chat_turn_id, scope, cursor, used_chunk_ids, quantity,
                          new_progression=False):
        """Estado opaco JSON de recuperação + quantidade cumulativa; sem commit.

        Deve ser salvo na mesma transação do turno. Scope é canônico; cursor
        versionado do RetrievalService mantém owner/escopo/modo no binding.
        Nenhum escopo equivalente pode reiniciar quantidade/chunks usados.
        `new_progression=True` só para o turno que abre uma progressão nova
        (pedido que não é gerar_mais): substitui o estado anterior, sem herdar
        cursor nem exigir que ele seja válido; o padrão continua sem reset.
        """
        for value in (owner_id,session_id,chat_turn_id):
            require_id(value)
        if isinstance(quantity,bool) or not isinstance(quantity,int) or quantity<0:
            raise ValueError('Quantidade inválida')
        if not isinstance(scope,dict) or (cursor is not None and not isinstance(cursor,dict)):
            raise ValueError('Escopo/cursor deve ser objeto JSON')
        original_scope=scope
        scope=self.canonicalize_scope(owner_id,scope)
        ids=list(used_chunk_ids)
        if any(not isinstance(i,str) or not i.strip() for i in ids):
            raise ValueError('Identificador de chunk inválido')
        if len(set(ids))!=len(ids):
            raise ValueError('Chunk repetido no estado')
        ids=sorted(ids)
        self._owned_chunks(owner_id,ids)
        for chunk_id in ids:
            chunk=self.lineage.get_chunk(chunk_id)
            prefix=scope['section_prefix']
            if chunk.document_version_id not in scope['document_version_ids'] or list(chunk.section_path[:len(prefix)])!=prefix:
                raise PermissionError('Chunk fora do escopo')
        cursor=self._canonical_cursor(owner_id,session_id,chat_turn_id,original_scope,scope,cursor,ids)
        self._lock_session(owner_id,session_id)
        if type(new_progression) is not bool:
            raise ValueError('new_progression deve ser booleano')
        previous=None if new_progression else self.get_continuation(owner_id,session_id)
        if previous is not None and previous['scope']==scope and (
                quantity<previous['quantity'] or not set(previous['used_chunk_ids']).issubset(ids)):
            raise ValueError('Continuação não pode perder progresso no mesmo escopo')
        state=dict(scope=scope,cursor=cursor,used_chunk_ids=ids,quantity=quantity)
        statement=insert(t.chat_continuation).values(owner_id=owner_id,session_id=session_id,
            chat_turn_id=chat_turn_id,state=state)
        self.connection.execute(statement.on_conflict_do_update(index_elements=['owner_id','session_id'],
            set_={'chat_turn_id':chat_turn_id,'state':state}))

    def _lock_session(self, owner_id, session_id):
        # JSON evita colisão de concatenação owner/session; colisão do hash só serializa.
        import json
        self.connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
            {'key':'chat-session:'+json.dumps([owner_id,session_id],ensure_ascii=True)})

    def get_continuation_head(self, owner_id, session_id):
        """chat_turn_id da continuação atual, serializado até o fim da transação.

        Não valida o estado: serve de fence de sessão mesmo quando o cursor
        antigo está corrompido e será substituído por uma progressão nova.
        """
        require_id(owner_id)
        require_id(session_id)
        self._lock_session(owner_id,session_id)
        return self.connection.execute(select(t.chat_continuation.c.chat_turn_id).where(
            t.chat_continuation.c.owner_id==owner_id,t.chat_continuation.c.session_id==session_id)).scalar_one_or_none()

    def get_continuation(self, owner_id, session_id):
        """Leitura serializada até fim da transação, inclusive quando ainda ausente."""
        require_id(owner_id)
        require_id(session_id)
        self._lock_session(owner_id,session_id)
        row=self.connection.execute(select(t.chat_continuation).where(
            t.chat_continuation.c.owner_id==owner_id,t.chat_continuation.c.session_id==session_id)).mappings().first()
        if row is None:
            return None
        state=dict(row['state'])
        original_scope=state['scope']
        state['scope']=self.canonicalize_scope(owner_id,original_scope)
        state['used_chunk_ids']=sorted(state['used_chunk_ids'])
        state['cursor']=self._canonical_cursor(owner_id,session_id,row['chat_turn_id'],
            original_scope,state['scope'],state['cursor'],state['used_chunk_ids'])
        return dict(state,chat_turn_id=row['chat_turn_id'])
