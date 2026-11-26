"""Orçamento de requisições dos testes que optam por APIs Gemini reais."""
from functools import wraps
from threading import Lock
from time import monotonic, sleep

import pytest


_gemini_request_lock = Lock()
_next_gemini_request = 0.0


@pytest.fixture
def gemini_request_budget(monkeypatch):
    """Espaça requisições reais em seis segundos; nunca repete uma falha do provedor.

    Orçamento conservador da suíte sequencial, não uma afirmação de quota da conta.
    Dublês locais e o comportamento de produção não são alterados.
    """
    from training_llm import GeminiModels

    def paced(method):
        @wraps(method)
        def call(*args, **kwargs):
            global _next_gemini_request
            with _gemini_request_lock:
                delay = _next_gemini_request - monotonic()
                if delay > 0:
                    sleep(delay)
                _next_gemini_request = monotonic() + 6.0
            return method(*args, **kwargs)
        return call

    for name in ("_embed", "judge_injection"):
        monkeypatch.setattr(GeminiModels, name, paced(getattr(GeminiModels, name)))
