"""Instrumentação opt-in da única execução de diagnóstico, sem mudar resultados."""
from functools import wraps
import json
from pathlib import Path

from sqlalchemy import select
from training_lineage import tables as t
from training_lineage.chat import ChatLineageRepository
from training_generation.chat import ChatPipeline

OUTPUT = Path('governanca/mudancas/2026-11-26-apps-api-autenticacao/evidencias/T-025/diagnostico-gerar-mais-dados.json')
ORIGINAL = ChatPipeline.run
DATA = []


def write():
    OUTPUT.write_text(json.dumps(DATA, ensure_ascii=False, indent=2, default=str))


def pytest_configure(config):
    @wraps(ORIGINAL)
    async def observed(self, owner_id, session_id, message, **kwargs):
        entry = {'ordinal': len(DATA) + 1, 'owner': owner_id, 'session': session_id,
                 'message': message, 'run_kwargs': kwargs, 'progress_is_none': self.progress is None}
        with self.engine.begin() as connection:
            entry['continuation_before'] = ChatLineageRepository(connection).get_continuation(owner_id, session_id)
            entry['prior_items_before'] = list(connection.execute(select(t.chat_item.c.record)).scalars())
            entry['chunks_before'] = [dict(row) for row in connection.execute(select(t.chunk)).mappings()]
        DATA.append(entry)
        write()
        response = await ORIGINAL(self, owner_id, session_id, message, **kwargs)
        entry['response'] = response.model_dump(mode='json')
        entry['execution'] = self.last_execution
        with self.engine.begin() as connection:
            entry['continuation_after'] = ChatLineageRepository(connection).get_continuation(owner_id, session_id)
            entry['items_after'] = list(connection.execute(select(t.chat_item.c.record).where(
                t.chat_item.c.chat_turn_id == response.chat_turn_id)).scalars())
            entry['retrieval_after'] = list(connection.execute(select(t.chat_retrieval.c.record).where(
                t.chat_retrieval.c.chat_turn_id == response.chat_turn_id)).scalars())
        write()
        return response
    ChatPipeline.run = observed


def pytest_unconfigure(config):
    ChatPipeline.run = ORIGINAL
