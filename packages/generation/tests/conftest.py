"""Dublês de teste do serviço de geração. Restritos a este diretório de testes (regra dura 1).

`fake_gemini` substitui só o transporte do modelo Gemini do ADK: Runner, LlmAgent, montagem de
requisição e eventos são os reais. `fake_litellm` substitui só `litellm.acompletion`.
"""

from types import SimpleNamespace

import pytest

from training_contracts import ContextChunk, NumberedContext

# Valores fictícios usados só para provar que credenciais nunca chegam a prompts.
GEMINI_SECRET = "gem-test-secret-" + "a1" * 12
OPENROUTER_SECRET = "or-test-secret-" + "b2" * 12


def env(**over):
    base = {
        "PROMPT_SOURCE": "offline",
        "GEMINI_API_KEY": GEMINI_SECRET,
        "OPENROUTER_API_KEY": OPENROUTER_SECRET,
        "GENERATION_MODEL": "gemini-3.1-flash-lite",
        "INPUT_JUDGE_MODEL": "gemini/gemini-3.1-flash-lite",
        "OUTPUT_JUDGE_MODEL": "openrouter/anthropic/claude-haiku-5.5",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def settings():
    from training_generation.settings import GenerationSettings

    return GenerationSettings.from_mapping(env())


@pytest.fixture
def context():
    return NumberedContext(chunks=[
        ContextChunk(ref="C1", chunk_id="chunk-a", page=12, section_path=["2", "2.3 Fase clara"],
                     text="Na fase clara da fotossíntese, a luz é absorvida pela clorofila e a água é quebrada, liberando oxigênio."),
        ContextChunk(ref="C2", chunk_id="chunk-b", page=13, section_path=["2", "2.4 Fase escura"],
                     text="O ciclo de Calvin fixa o CO2 usando ATP e NADPH produzidos na fase clara."),
    ])


@pytest.fixture
def fake_gemini(monkeypatch):
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    rec = SimpleNamespace(requests=[], responses=[])

    async def generate_content_async(self, llm_request, stream=False):
        rec.requests.append(SimpleNamespace(model=self, request=llm_request))
        resposta = rec.responses.pop(0)
        if isinstance(resposta, BaseException):
            raise resposta
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=resposta)]))

    monkeypatch.setattr(Gemini, "generate_content_async", generate_content_async)
    return rec


@pytest.fixture
def fake_litellm(monkeypatch):
    import litellm

    rec = SimpleNamespace(calls=[], responses=[])

    async def acompletion(**kwargs):
        rec.calls.append(kwargs)
        resposta = rec.responses.pop(0)
        if isinstance(resposta, BaseException):
            raise resposta
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=resposta))])

    monkeypatch.setattr(litellm, "acompletion", acompletion)
    return rec


def _request_text(llm_request) -> str:
    partes = [str(llm_request.config.system_instruction or "")]
    for content in llm_request.contents or []:
        for part in content.parts or []:
            partes.append(part.text or "")
    return "\n".join(partes)


@pytest.fixture
def request_text():
    """Todo texto enviado ao modelo numa requisição ADK: instrução de sistema e conteúdos."""
    return _request_text


@pytest.fixture
def secrets():
    return (GEMINI_SECRET, OPENROUTER_SECRET)


@pytest.fixture
def make_env():
    return env
