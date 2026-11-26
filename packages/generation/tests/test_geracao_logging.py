"""S2: guarda de logging isolada, sem handlers do pytest mascarando o `logging.lastResort`.

A fixture `logging_isolado` fotografa todo o `logging.Logger.manager` antes do teste e o restaura
depois, conferindo o resultado (S2-R da reavaliação OpenAI):

- loggers e PlaceHolders preexistentes: mesmo objeto em `loggerDict`, nível, filtros, handlers,
  `propagate`, `disabled`, `parent` e `loggerMap`, inclusive nível e filtros internos de cada
  handler alcançável (raiz, loggers e `lastResort`), além de `manager.disable` e `LITELLM_LOG`;
- loggers novos criados pelo teste (nome com `TAG`) ou pela guarda e sem referência de módulo:
  removidos da hierarquia;
- loggers novos de biblioteca, criados por import tardio do SDK e referenciados pelo módulo que os
  importou: mantidos, porque apagá-los desligaria o logger da biblioteca, mas neutralizados (sem
  os filtros da guarda em si nem nos seus handlers).

Os artefatos fictícios (loggers, handlers e marcador) existem só neste arquivo de testes.
"""

import io
import logging
import os
import sys
from contextlib import contextmanager
from uuid import uuid4

import pytest

from training_generation.settings import _ADK_FILTER, _ADK_LOGGERS, guard_logging
from training_llm import _SDK_FILTER, _SDK_LOGGERS

MARCADOR = "MARCADOR_PRIVADO_DO_PROVEDOR"
TAG = "teste_logging_t019"
"""Marca no nome de todo logger criado por estes testes."""

_FILTROS_DA_GUARDA = (_ADK_FILTER, _SDK_FILTER)


def _nome_de_teste(sufixo: str) -> str:
    return f"{sufixo}.{TAG}_{uuid4().hex}"


def _handlers_alcancaveis(manager) -> dict[int, logging.Handler]:
    loggers = [manager.root] + [lg for lg in manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    handlers = {id(h): h for lg in loggers for h in lg.handlers}
    if logging.lastResort is not None:
        handlers[id(logging.lastResort)] = logging.lastResort
    return handlers


def _foto() -> dict:
    manager = logging.Logger.manager
    entradas = {}
    for nome, obj in manager.loggerDict.items():
        if isinstance(obj, logging.Logger):
            entradas[nome] = ("logger", obj, obj.level, tuple(obj.filters), tuple(obj.handlers),
                              obj.propagate, obj.disabled, obj.parent)
        else:
            entradas[nome] = ("placeholder", obj, tuple(obj.loggerMap))
    raiz = manager.root
    return {
        "entradas": entradas,
        "raiz": (raiz.level, tuple(raiz.filters), tuple(raiz.handlers), raiz.propagate, raiz.disabled),
        "handlers": {k: (h, h.level, tuple(h.filters)) for k, h in _handlers_alcancaveis(manager).items()},
        "last_resort": logging.lastResort,
        "disable": manager.disable,
        "litellm_log": os.environ.get("LITELLM_LOG"),
    }


def _referenciados_por_modulos(candidatos: dict[str, logging.Logger]) -> set[str]:
    alvo = {id(lg): nome for nome, lg in candidatos.items()}
    achados = set()
    for modulo in list(sys.modules.values()):
        for valor in list(getattr(modulo, "__dict__", {}).values()):
            if id(valor) in alvo:
                achados.add(alvo[id(valor)])
    return achados


def _ancestral_logger(manager, nome: str, placeholders: list | None = None) -> logging.Logger:
    """Ancestral real mais próximo, como `_fixupParents`; coleta PlaceHolders no caminho."""
    partes = nome.split(".")
    for i in range(len(partes) - 1, 0, -1):
        ancestral = manager.loggerDict.get(".".join(partes[:i]))
        if isinstance(ancestral, logging.Logger):
            return ancestral
        if isinstance(ancestral, logging.PlaceHolder) and placeholders is not None:
            placeholders.append(ancestral)
    return manager.root


def _reconstruir_hierarquia(manager, foto: dict, mantidos: set[str]) -> None:
    """Mapas e pais originais; só os loggers de biblioteca mantidos são encaixados neles."""
    entradas = foto["entradas"]
    for nome, entrada in entradas.items():
        if entrada[0] == "placeholder":
            # O loggerMap do stdlib é histórico (maior que a hierarquia mínima): volta inteiro.
            entrada[1].loggerMap = dict.fromkeys(entrada[2])
    for nome, obj in manager.loggerDict.items():
        if isinstance(obj, logging.PlaceHolder) and nome not in entradas:
            obj.loggerMap = {}
    for nome in sorted(mantidos, key=lambda n: n.count(".")):
        logger = manager.loggerDict[nome]
        caminho: list = []
        logger.parent = _ancestral_logger(manager, nome, caminho)
        for placeholder in caminho:
            placeholder.append(logger)
    for nome, entrada in entradas.items():
        if entrada[0] != "logger":
            continue
        pai_original, pai_atual = entrada[7], _ancestral_logger(manager, nome)
        # Mantém o pai original, salvo se um logger de biblioteca mantido ficou mais próximo.
        entrada[1].parent = pai_atual if pai_atual.name in mantidos else pai_original


def _restaurar(foto: dict) -> list[str]:
    """Restaura a foto e devolve os loggers novos de biblioteca mantidos (neutralizados)."""
    manager = logging.Logger.manager
    entradas = foto["entradas"]
    novos = {n: o for n, o in manager.loggerDict.items() if n not in entradas or o is not entradas[n][1]}
    novos_loggers = {n: o for n, o in novos.items() if isinstance(o, logging.Logger) and TAG not in n}
    mantidos = _referenciados_por_modulos(novos_loggers)
    # Ancestral novo de um logger de biblioteca mantido também precisa ficar.
    for nome in list(mantidos):
        partes = nome.split(".")
        for k in range(1, len(partes)):
            ancestral = ".".join(partes[:k])
            if isinstance(novos_loggers.get(ancestral), logging.Logger):
                mantidos.add(ancestral)
    for nome in list(novos):
        if nome in entradas:
            manager.loggerDict[nome] = entradas[nome][1]  # PlaceHolder/Logger original
        elif nome not in mantidos:
            del manager.loggerDict[nome]
    # PlaceHolders novos só sobrevivem se ainda são ancestrais de um logger mantido.
    for nome in [n for n, o in manager.loggerDict.items()
                 if isinstance(o, logging.PlaceHolder) and n not in entradas]:
        if not any(m.startswith(nome + ".") for m in mantidos):
            del manager.loggerDict[nome]
    for nome in mantidos:
        logger = manager.loggerDict[nome]
        logger.filters = [f for f in logger.filters if f not in _FILTROS_DA_GUARDA]
        for handler in logger.handlers:
            handler.filters = [f for f in handler.filters if f not in _FILTROS_DA_GUARDA]
    for nome, entrada in entradas.items():
        if entrada[0] == "logger":
            _, logger, level, filters, handlers, propagate, disabled, _parent = entrada
            logger.level, logger.filters, logger.handlers = level, list(filters), list(handlers)
            logger.propagate, logger.disabled = propagate, disabled
    raiz = manager.root
    raiz.level, raiz.filters, raiz.handlers, raiz.propagate, raiz.disabled = (
        foto["raiz"][0], list(foto["raiz"][1]), list(foto["raiz"][2]), foto["raiz"][3], foto["raiz"][4])
    logging.lastResort = foto["last_resort"]
    for handler, level, filters in foto["handlers"].values():
        handler.level, handler.filters = level, list(filters)
    manager.disable = foto["disable"]
    if foto["litellm_log"] is None:
        os.environ.pop("LITELLM_LOG", None)
    else:
        os.environ["LITELLM_LOG"] = foto["litellm_log"]
    _reconstruir_hierarquia(manager, foto, mantidos)
    manager._clear_cache()
    return sorted(mantidos)


def _conferir(antes: dict, mantidos: list[str]) -> None:
    depois = _foto()
    manager = logging.Logger.manager
    for nome, entrada in antes["entradas"].items():
        atual = depois["entradas"].get(nome)
        assert atual is not None, f"logger preexistente removido: {nome}"
        if entrada[0] == "placeholder":
            # loggerMap pode ganhar só os loggers de biblioteca mantidos sob ele.
            assert atual[1] is entrada[1]
            assert set(atual[2]) - set(entrada[2]) <= {manager.loggerDict[m] for m in mantidos}
            assert set(entrada[2]) <= set(atual[2])
        else:
            assert atual[:7] == entrada[:7], f"estado divergente: {nome}"
            # O pai só muda se um logger de biblioteca mantido entrou entre ele e o pai antigo.
            assert atual[7] is entrada[7] or atual[7].name in mantidos, f"parent divergente: {nome}"
    for chave in ("raiz", "last_resort", "disable", "litellm_log"):
        assert depois[chave] == antes[chave], chave
    for chave, (handler, level, filters) in antes["handlers"].items():
        assert (handler.level, tuple(handler.filters)) == (level, filters), f"handler alterado: {handler!r}"
    assert not [n for n in manager.loggerDict if TAG in n], "logger do teste permaneceu"
    for nome in set(manager.loggerDict) - set(antes["entradas"]):
        obj = manager.loggerDict[nome]
        if isinstance(obj, logging.Logger):
            assert nome in mantidos
            assert not set(obj.filters) & set(_FILTROS_DA_GUARDA)
            assert not any(set(h.filters) & set(_FILTROS_DA_GUARDA) for h in obj.handlers)


@pytest.fixture
def logging_isolado():
    """Foto/restauro do estado global; o gerenciador `sem_handlers` remove os handlers da raiz."""
    antes = _foto()
    raiz = logging.getLogger()

    @contextmanager
    def sem_handlers():
        # O pytest instala LogCaptureHandler na raiz na fase de chamada, depois do setup das
        # fixtures; por isso a remoção acontece aqui, dentro do corpo do teste.
        dentro = (raiz.level, list(raiz.handlers))
        raiz.handlers = []
        raiz.setLevel(logging.WARNING)
        assert logging.getLogger().handlers == [] and logging.lastResort is not None
        try:
            yield
        finally:
            raiz.level, raiz.handlers = dentro[0], dentro[1]

    yield sem_handlers
    mantidos = _restaurar(antes)
    _conferir(antes, mantidos)


@pytest.fixture
def logger_do_usuario():
    """Logger fictício preexistente, com handler e filtro próprios; deve sobreviver intacto."""
    nome = f"usuario_ficticio_{uuid4().hex}"
    logger = logging.getLogger(nome)
    destino = io.StringIO()
    handler = logging.StreamHandler(destino)
    filtro_handler, filtro_logger = logging.Filter("x"), logging.Filter("y")
    handler.addFilter(filtro_handler)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addFilter(filtro_logger)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    esperado = (logger.level, [filtro_logger], [handler], False, handler.level, [filtro_handler])
    yield logger
    # Teardown após o de `logging_isolado` (ordem inversa de setup).
    assert logging.Logger.manager.loggerDict.get(nome) is logger
    assert (logger.level, logger.filters, logger.handlers, logger.propagate,
            handler.level, handler.filters) == esperado
    del logging.Logger.manager.loggerDict[nome]
    logging.Logger.manager._clear_cache()


def test_S2_controle_lastresort_ativo_sem_handlers(logging_isolado, capsys):
    with logging_isolado():
        # Controle negativo: sem handlers, um logger fora do ADK chega ao stderr pelo lastResort.
        guard_logging()
        logging.getLogger(_nome_de_teste("aplicacao")).error("controle %s", MARCADOR)
        assert f"controle {MARCADOR}" in capsys.readouterr().err


def test_S2_filho_adk_criado_apos_guarda_nao_chega_ao_lastresort(logging_isolado, capsys):
    with logging_isolado():
        guard_logging()
        nome = _nome_de_teste("google_adk.google.adk")
        assert nome not in logging.Logger.manager.loggerDict
        filho = logging.getLogger(nome)  # criado somente depois da guarda, nível herdado
        assert not filho.filters
        try:
            raise RuntimeError(MARCADOR)
        except RuntimeError:
            filho.exception("Node execution failed with exception: %s", MARCADOR)
        filho.critical(MARCADOR)
        capturado = capsys.readouterr()
        assert MARCADOR not in capturado.err and MARCADOR not in capturado.out


def test_S2_lastresort_filtra_registro_adk_mesmo_com_nivel_explicito(logging_isolado, capsys):
    with logging_isolado():
        guard_logging()
        filho = logging.getLogger(_nome_de_teste("google.adk"))
        filho.setLevel(logging.ERROR)  # nível explícito contorna o nível dos ancestrais
        filho.error(MARCADOR)
        assert _ADK_FILTER in logging.lastResort.filters
        assert MARCADOR not in capsys.readouterr().err


def test_S2_filho_com_handler_proprio_e_filtrado_na_proxima_guarda(logging_isolado, capsys):
    with logging_isolado():
        guard_logging()
        destino = io.StringIO()
        filho = logging.getLogger(_nome_de_teste("google_adk"))
        filho.setLevel(logging.ERROR)
        filho.addHandler(logging.StreamHandler(destino))
        # Cada chamada de LLM do serviço executa a guarda de novo antes de entregar dados ao SDK.
        guard_logging()
        filho.error(MARCADOR)
        assert MARCADOR not in destino.getvalue()
        assert MARCADOR not in capsys.readouterr().err


def test_S2_excecao_real_do_adk_sem_handlers_nao_vaza(logging_isolado, capsys, settings, fake_gemini):
    with logging_isolado():
        import asyncio

        from google.genai import errors as genai_errors

        from training_generation.agents import GenerationError, GenerationService

        service = GenerationService(settings)
        fake_gemini.responses.append(genai_errors.ClientError(
            400, {"error": {"code": 400, "message": MARCADOR, "status": "INVALID_ARGUMENT"}}))
        with pytest.raises(GenerationError):
            asyncio.run(service.classify("x"))
        capturado = capsys.readouterr()
        assert MARCADOR not in capturado.err and MARCADOR not in capturado.out


def test_S2_sensibilidade_sem_filtro_o_vazamento_aparece(logging_isolado, capsys):
    # Controle: removendo a proteção do lastResort, o mesmo registro ADK chega ao stderr; logo a
    # ausência do marcador nos testes acima não decorre de captura mascarada.
    with logging_isolado():
        guard_logging()
        logging.lastResort.removeFilter(_ADK_FILTER)
        filho = logging.getLogger(_nome_de_teste("google_adk"))
        filho.setLevel(logging.ERROR)
        filho.error(MARCADOR)
        assert MARCADOR in capsys.readouterr().err


# S2-R — prova do restauro real do estado global

def test_S2R_restauro_preserva_logger_do_usuario_e_desfaz_alteracoes(logger_do_usuario, logging_isolado):
    handler = logger_do_usuario.handlers[0]
    with logging_isolado():
        guard_logging()
        logger_do_usuario.setLevel(logging.CRITICAL)
        logger_do_usuario.addFilter(logging.Filter("alterado"))
        handler.addFilter(_ADK_FILTER)
        handler.setLevel(logging.ERROR)
        logging.getLogger("google_adk").addHandler(logging.StreamHandler(io.StringIO()))
        logging.Logger.manager.disable = logging.ERROR
    # Conferência: teardown de `logging_isolado` (`_conferir`) e, depois, de `logger_do_usuario`.


def test_S2R_restauro_explicito_remove_novos_e_reconstroi_hierarquia(logging_isolado):
    manager = logging.Logger.manager
    preexistentes = dict(manager.loggerDict)
    pais = {n: o.parent for n, o in preexistentes.items() if isinstance(o, logging.Logger)}
    foto = _foto()
    intermediario = _nome_de_teste("google_adk.intermediario")
    neto = logging.getLogger(intermediario + ".neto")  # cria PlaceHolder novo
    meio = logging.getLogger(intermediario)  # converte o PlaceHolder em logger e reparenta o neto
    assert neto.parent is meio
    guard_logging()
    mantidos = _restaurar(foto)
    _conferir(foto, mantidos)
    assert intermediario not in manager.loggerDict and intermediario + ".neto" not in manager.loggerDict
    assert all(manager.loggerDict.get(n) is o for n, o in preexistentes.items())
    assert all(manager.loggerDict[n].parent is p or manager.loggerDict[n].parent.name in mantidos
               for n, p in pais.items())
    # getLogger devolve um objeto novo e limpo: nenhuma referência ao logger removido.
    novo = logging.getLogger(intermediario)
    assert novo is not meio and novo.filters == [] and novo.handlers == [] and novo.level == logging.NOTSET
    del manager.loggerDict[intermediario]
    manager._clear_cache()
