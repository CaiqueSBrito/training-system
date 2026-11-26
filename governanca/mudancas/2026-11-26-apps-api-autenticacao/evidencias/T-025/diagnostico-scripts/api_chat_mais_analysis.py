"""Análise offline determinística: dublê existente + E9, sem executar pytest."""
import ast
import asyncio
from difflib import SequenceMatcher
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
from uuid import UUID

from training_contracts import ContextChunk, NumberedContext, validar_item
from training_generation.validation import DUPLICATE_RATIO, _similarity_key, validate_items
from training_ingestion import DoclingParser

ROOT = Path('governanca/mudancas/2026-11-26-apps-api-autenticacao/evidencias/T-025')


def main():
    records = json.loads((ROOT / 'diagnostico-gerar-mais-dados.json').read_text())
    third = records[2]
    chunks = sorted(third['chunks_before'], key=lambda c: c['start_offset'])
    parsed_text = ''.join(c['text'] for c in chunks)
    assert [c['start_offset'] for c in chunks] == [0, len(chunks[0]['text']), len(chunks[0]['text']) + len(chunks[1]['text'])]
    parser = DoclingParser()

    def chunk_id(chunk, version):
        canonical = sha256(json.dumps([version, parser.canonical_version, parser.version], separators=(',', ':')).encode()).hexdigest()
        identity = json.dumps([version, canonical, sha256(parsed_text.encode()).hexdigest(), chunk['chunker_version'],
            chunk['start_offset'], chunk['end_offset'], chunk['text']], ensure_ascii=False, separators=(',', ':'))
        return sha256(identity.encode()).hexdigest()

    assert all(chunk_id(c, c['document_version_id']) == c['chunk_id'] for c in chunks)
    spec = importlib.util.spec_from_file_location('t021_existing_test', 'packages/generation/tests/test_chat_integration.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    async def evaluate(version):
        api = module.APIs()
        questions = []
        context = None
        for c in chunks[:2]:
            context = NumberedContext(chunks=[ContextChunk(ref='C1', chunk_id=chunk_id(c, version),
                text=c['text'], page=c['page'], section_path=c['section_path'])])
            raw = await api.generate(None, None, None, context, 1, item_type='question')
            questions.append(validar_item(raw.itens[0]))
        verdict = validate_items(raw, context, expected_type='question', prior=[questions[0]]).verdicts[0]
        return {'version': version, 'prefixes': [chunk_id(c, version)[:8] for c in chunks[:2]],
            'stems': [q.stem for q in questions],
            'similarity': SequenceMatcher(None, _similarity_key(questions[1])[1], _similarity_key(questions[0])[1]).ratio(),
            'verdict': verdict.model_dump(mode='json')}

    actual = asyncio.run(evaluate(chunks[0]['document_version_id']))
    # Experimento fechado: todos os 256 UUIDs definidos a priori, sem buscar uma
    # suíte verde nem repetir o teste. Somente hashes de identidade mudam.
    experiment = [asyncio.run(evaluate(str(UUID(int=i, version=4)))) for i in range(1, 257)]
    assert any(x['verdict']['aprovado'] for x in experiment)
    assert any(x['verdict']['motivo'] == 'duplicata' for x in experiment)

    old = ast.parse(subprocess.check_output(['git', 'show', 'b3b2969:packages/generation/src/training_generation/chat.py'], text=True))
    new = ast.parse(Path('packages/generation/src/training_generation/chat.py').read_text())
    old_class = next(n for n in old.body if isinstance(n, ast.ClassDef) and n.name == 'ChatPipeline')
    new_class = next(n for n in new.body if isinstance(n, ast.ClassDef) and n.name == 'ChatPipeline')
    old_run = next(n for n in old_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'run')
    new_run = next(n for n in new_class.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_run')
    ast_equal = ast.dump(ast.Module(body=old_run.body, type_ignores=[])) == ast.dump(ast.Module(body=new_run.body, type_ignores=[]))
    result = {'old_run_body_equals_new_run_body_ast': ast_equal,
        'instrumented_test_progress_is_none': [r['progress_is_none'] for r in records],
        'chunk_hash_recipe_matches_all_recorded_chunks': True, 'duplicate_threshold_unchanged': DUPLICATE_RATIO,
        'actual_diagnostic_pair': actual, 'closed_experiment': experiment,
        'experiment_counts': {'accepted': sum(x['verdict']['aprovado'] for x in experiment),
                              'duplicate': sum(x['verdict']['motivo'] == 'duplicata' for x in experiment)}}
    (ROOT / 'diagnostico-gerar-mais-analise.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'closed_experiment'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
