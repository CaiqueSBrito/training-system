"""Smoke real, uma execução: ADK + Gemini no planejamento (E3, E4) e juiz de saída OpenRouter (E10).

Orçamento fixo: no máximo 3 chamadas reais, espaçadas, sem retentativa. Sem credencial, o teste é
pulado (status Não executado). 429/503 aparecem como falha externa sanitizada; não repetir até
passar, nem trocar o modelo do juiz se ele estiver indisponível.
"""

import asyncio
import time

import pytest

from training_contracts import Classification, ContextChunk, Flashcard, Intent, NumberedContext, SearchPlan
from training_generation import ConfiguracaoInvalida, GenerationService, GenerationSettings

_MAX_CHAMADAS = 3
_INTERVALO = 6.0


@pytest.fixture(scope="module")
def real_budget():
    estado = {"chamadas": 0, "proxima": 0.0}

    async def admitir():
        assert estado["chamadas"] < _MAX_CHAMADAS, "orçamento de chamadas reais esgotado"
        espera = estado["proxima"] - time.monotonic()
        if espera > 0:
            await asyncio.sleep(espera)
        estado["chamadas"] += 1
        estado["proxima"] = time.monotonic() + _INTERVALO
    return admitir


@pytest.fixture(scope="module")
def real_service():
    mp = pytest.MonkeyPatch()
    mp.setenv("PROMPT_SOURCE", "offline")
    try:
        settings = GenerationSettings.from_env()
    except ConfiguracaoInvalida as exc:
        pytest.skip(f"configuração real indisponível: {exc}")
    finally:
        mp.undo()
    return GenerationService(settings)


def test_smoke_real_adk_classifica_e_planeja(real_service, real_budget):
    async def fluxo():
        await real_budget()
        classificacao = await real_service.classify("Crie 3 flashcards sobre fotossíntese")
        assert isinstance(classificacao, Classification)
        assert classificacao.intencao is Intent.GERAR_FLASHCARDS
        await real_budget()
        plano = await real_service.plan("Crie 3 flashcards sobre fotossíntese", classificacao)
        assert isinstance(plano, SearchPlan) and 1 <= len(plano.consultas) <= 3
    asyncio.run(fluxo())


def test_smoke_real_juiz_de_saida_openrouter(real_service, real_budget):
    contexto = NumberedContext(chunks=[ContextChunk(
        ref="C1", chunk_id="smoke-c1", page=1, section_path=["Fotossíntese"],
        text="Na fase clara da fotossíntese, a luz é absorvida pela clorofila.")])
    item = Flashcard(front="O que absorve a luz na fase clara da fotossíntese?", back="A clorofila.",
                     bloom_level="lembrar",
                     citations=[{"chunk_ref": "C1", "evidence": "a luz é absorvida pela clorofila"}])

    async def fluxo():
        await real_budget()
        resultado = await real_service.judge([item], contexto)
        assert [v.item_index for v in resultado.vereditos] == [0]
        print(f"veredito_real fundamentado={resultado.vereditos[0].fundamentado} "
              f"conforme_rubrica={resultado.vereditos[0].conforme_rubrica}")
    asyncio.run(fluxo())
