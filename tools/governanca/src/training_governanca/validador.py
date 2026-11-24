"""Confere `governanca/` contra as regras do pipeline.

Regras de origem: `governanca/pipeline.md`, `governanca/instrucoes/rastreabilidade.md`
e os campos documentados em `governanca/modelos/`.

Uso: `validar-governanca [raiz]`. Sem argumento, sobe a partir do diretório atual
até achar a pasta `governanca/`. Sai com 1 se houver erro.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROVEDORES = frozenset({"anthropic", "openai", "google"})
TRILHAS = frozenset({"padrao", "llm"})
ESTADOS_MUDANCA = frozenset({"proposta", "aprovada", "em-andamento", "concluida", "cancelada"})
EXIGEM_APROVACAO = frozenset({"aprovada", "em-andamento", "concluida"})
ESTADOS_TAREFA = frozenset(
    {
        "entender",
        "planejar",
        "aguardando-permissao",
        "construir",
        "provar",
        "revisar",
        "concluida",
        "bloqueada",
        "cancelada",
    }
)
EXIGEM_PERMISSAO = frozenset({"construir", "provar", "revisar", "concluida"})
ENCERRADOS = frozenset({"concluida", "cancelada"})
STATUS_EVIDENCIA = ("Passou", "Falhou", "Não executado", "Bloqueado", "Não se aplica")
STATUS_QUE_FECHAM = frozenset({"Passou", "Não se aplica"})
SECOES_DELTA = ("ADICIONA", "MODIFICA", "REMOVE")

RE_ID_REQUISITO = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{3}")
RE_TITULO_REQUISITO = re.compile(rf"^###\s+({RE_ID_REQUISITO.pattern})\b", re.MULTILINE)
RE_SECAO = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
RE_TAREFA = re.compile(r"T-\d{3,}")
RE_MUDANCA = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*")

Erros = list[tuple[Path, str]]


@dataclass
class Mudanca:
    pasta: Path
    arquivada: bool
    estado: str | None
    requisitos_conhecidos: set[str]


@dataclass
class Tarefa:
    id: str
    caminho: Path
    estado: str | None
    depende_de: list[str] = field(default_factory=list)


@dataclass
class Resultado:
    erros: list[str]
    mudancas: int
    tarefas: int
    requisitos: int


def _texto_nao_vazio(valor: object) -> bool:
    return isinstance(valor, str) and bool(valor.strip())


def _data_valida(valor: object) -> bool:
    if isinstance(valor, dt.datetime):
        return False
    if isinstance(valor, dt.date):
        return True
    if isinstance(valor, str):
        try:
            dt.date.fromisoformat(valor)
        except ValueError:
            return False
        return True
    return False


def _ler_frontmatter(caminho: Path, erros: Erros) -> dict | None:
    texto = caminho.read_text(encoding="utf-8")
    if not texto.startswith("---\n"):
        erros.append((caminho, "sem frontmatter YAML (o arquivo deve começar com ---)"))
        return None
    fim = texto.find("\n---", 3)
    if fim == -1:
        erros.append((caminho, "frontmatter YAML sem o --- de fechamento"))
        return None
    try:
        dados = yaml.safe_load(texto[4:fim])
    except yaml.YAMLError as erro:
        erros.append((caminho, f"frontmatter YAML inválido: {erro}"))
        return None
    if not isinstance(dados, dict):
        erros.append((caminho, "frontmatter YAML deve ser um mapa de campos"))
        return None
    return dados


def _validar_registro_humano(valor: object, campo: str, caminho: Path, erros: Erros) -> None:
    """Aprovação de gate: só conta com autor humano, data e as palavras literais."""
    if not isinstance(valor, dict):
        erros.append((caminho, f"{campo} é obrigatório neste estado: {{ por: humano, em: AAAA-MM-DD, palavras: ... }}"))
        return
    if valor.get("por") != "humano":
        erros.append((caminho, f"{campo}.por deve ser 'humano'"))
    if not _data_valida(valor.get("em")):
        erros.append((caminho, f"{campo}.em deve ser uma data AAAA-MM-DD"))
    if not _texto_nao_vazio(valor.get("palavras")):
        erros.append((caminho, f"{campo}.palavras deve trazer a frase literal do humano"))


def _coletar_specs(governanca: Path, erros: Erros) -> set[str]:
    origem: dict[str, Path] = {}
    for spec in sorted((governanca / "specs").glob("*/spec.md")):
        for requisito in RE_TITULO_REQUISITO.findall(spec.read_text(encoding="utf-8")):
            if requisito in origem:
                erros.append((spec, f"requisito {requisito} duplicado (também em {origem[requisito]})"))
            else:
                origem[requisito] = spec
    return set(origem)


def _ler_delta(caminho: Path) -> dict[str, list[str]]:
    texto = caminho.read_text(encoding="utf-8")
    secoes: dict[str, list[str]] = {nome: [] for nome in SECOES_DELTA}
    marcas = list(RE_SECAO.finditer(texto))
    for indice, marca in enumerate(marcas):
        nome = marca.group(1).strip().upper()
        if nome not in secoes:
            continue
        fim = marcas[indice + 1].start() if indice + 1 < len(marcas) else len(texto)
        secoes[nome] = RE_TITULO_REQUISITO.findall(texto[marca.end() : fim])
    return secoes


def _validar_mudanca(pasta: Path, arquivada: bool, specs: set[str], erros: Erros) -> Mudanca:
    if not RE_MUDANCA.fullmatch(pasta.name):
        erros.append((pasta, "o nome da pasta deve ser AAAA-MM-DD-slug"))

    estado: str | None = None
    proposta = pasta / "proposta.md"
    if not proposta.is_file():
        erros.append((pasta, "falta proposta.md"))
    elif (frontmatter := _ler_frontmatter(proposta, erros)) is not None:
        if frontmatter.get("id") != pasta.name:
            erros.append((proposta, f"id deve ser igual ao nome da pasta ({pasta.name})"))
        if not _texto_nao_vazio(frontmatter.get("titulo")):
            erros.append((proposta, "titulo é obrigatório"))
        if frontmatter.get("trilha") not in TRILHAS:
            erros.append((proposta, f"trilha deve ser uma de: {', '.join(sorted(TRILHAS))}"))
        estado = frontmatter.get("estado")
        if estado not in ESTADOS_MUDANCA:
            erros.append((proposta, f"estado {estado!r} inválido; use: {', '.join(sorted(ESTADOS_MUDANCA))}"))
            estado = None
        elif estado in EXIGEM_APROVACAO:
            _validar_registro_humano(frontmatter.get("aprovacao"), "aprovacao", proposta, erros)
        if estado is not None:
            if arquivada and estado not in ENCERRADOS:
                erros.append((proposta, "mudança arquivada deve estar concluida ou cancelada"))
            if not arquivada and estado in ENCERRADOS:
                erros.append((proposta, f"mudança {estado}: mova a pasta para governanca/mudancas/arquivo/"))

    conhecidos = set(specs)
    delta = pasta / "delta.md"
    if delta.is_file():
        secoes = _ler_delta(delta)
        if not arquivada:
            for requisito in secoes["ADICIONA"]:
                if requisito in specs:
                    erros.append((delta, f"ADICIONA {requisito}, mas ele já existe na spec viva"))
            for nome in ("MODIFICA", "REMOVE"):
                for requisito in secoes[nome]:
                    if requisito not in specs:
                        erros.append((delta, f"{nome} {requisito}, mas ele não existe na spec viva"))
        conhecidos.update(secoes["ADICIONA"], secoes["MODIFICA"])
        if arquivada:
            # Tarefas arquivadas continuam citando requisitos que a própria mudança removeu.
            conhecidos.update(secoes["REMOVE"])
    return Mudanca(pasta, arquivada, estado, conhecidos)


def _validar_casos(casos: object, raiz: Path, caminho: Path, erros: Erros) -> None:
    """Gate G3: caso aprovado não pode mudar depois da aprovação."""
    if casos is None:
        return
    if not isinstance(casos, list):
        erros.append((caminho, "casos_aprovados deve ser uma lista"))
        return
    for caso in casos:
        if not isinstance(caso, dict) or not _texto_nao_vazio(caso.get("arquivo")):
            erros.append((caminho, "cada caso aprovado precisa de { arquivo, sha256, em }"))
            continue
        arquivo = caso["arquivo"]
        alvo = (raiz / arquivo).resolve()
        if not alvo.is_relative_to(raiz.resolve()):
            erros.append((caminho, f"caso aprovado {arquivo} aponta para fora do repositório"))
            continue
        if not _data_valida(caso.get("em")):
            erros.append((caminho, f"caso aprovado {arquivo}: em deve ser uma data AAAA-MM-DD"))
        if not alvo.is_file():
            erros.append((caminho, f"caso aprovado {arquivo} não existe"))
            continue
        atual = hashlib.sha256(alvo.read_bytes()).hexdigest()
        if caso.get("sha256") != atual:
            erros.append(
                (caminho, f"caso aprovado {arquivo} mudou depois da aprovação (sha256 registrado ≠ atual); alterar caso aprovado exige o humano")
            )


def _validar_evidencias(
    evidencias: object, requisitos: list[str], estado: str | None, mudanca: Path, caminho: Path, erros: Erros
) -> None:
    if evidencias is None:
        evidencias = []
    if not isinstance(evidencias, list):
        erros.append((caminho, "evidencias deve ser uma lista"))
        return
    ultima: dict[str, str] = {}
    for evidencia in evidencias:
        if not isinstance(evidencia, dict):
            erros.append((caminho, "cada evidência deve ser { criterio, metodo, status, ref }"))
            continue
        criterio = evidencia.get("criterio")
        status = evidencia.get("status")
        referencia = evidencia.get("ref")
        if criterio not in requisitos:
            erros.append((caminho, f"evidência para {criterio!r}, que não é requisito desta tarefa"))
        if not _texto_nao_vazio(evidencia.get("metodo")):
            erros.append((caminho, f"evidência de {criterio}: metodo é obrigatório"))
        if status not in STATUS_EVIDENCIA:
            erros.append((caminho, f"evidência de {criterio}: status {status!r} inválido; use: {', '.join(STATUS_EVIDENCIA)}"))
        elif status == "Não se aplica" and not _texto_nao_vazio(evidencia.get("motivo")):
            erros.append((caminho, f"evidência de {criterio}: 'Não se aplica' exige motivo"))
        if not _texto_nao_vazio(referencia):
            erros.append((caminho, f"evidência de {criterio}: ref é obrigatória"))
        elif referencia.startswith("evidencias/") and not (mudanca / referencia).exists():
            erros.append((caminho, f"evidência de {criterio}: {referencia} não existe na pasta da mudança"))
        if isinstance(criterio, str) and status in STATUS_EVIDENCIA:
            ultima[criterio] = status
    if estado == "concluida":
        for requisito in requisitos:
            if ultima.get(requisito) not in STATUS_QUE_FECHAM:
                atual = ultima.get(requisito, "nenhuma")
                erros.append(
                    (caminho, f"tarefa concluida exige que a última evidência de {requisito} seja Passou ou Não se aplica (atual: {atual})")
                )


def _validar_tarefa(caminho: Path, mudanca: Mudanca, papeis: set[str], raiz: Path, erros: Erros) -> Tarefa | None:
    frontmatter = _ler_frontmatter(caminho, erros)
    if frontmatter is None:
        return None

    identificador = frontmatter.get("id")
    if not isinstance(identificador, str) or not RE_TAREFA.fullmatch(identificador):
        erros.append((caminho, "id deve ser T-NNN"))
        identificador = None
    elif caminho.stem != identificador:
        erros.append((caminho, f"o arquivo deve se chamar {identificador}.md"))

    if frontmatter.get("mudanca") != mudanca.pasta.name:
        erros.append((caminho, f"mudanca deve ser {mudanca.pasta.name}"))
    if not _texto_nao_vazio(frontmatter.get("titulo")):
        erros.append((caminho, "titulo é obrigatório"))

    requisitos = frontmatter.get("requisitos")
    if not isinstance(requisitos, list) or not requisitos:
        erros.append((caminho, "requisitos deve ser uma lista não vazia"))
        requisitos = []
    for requisito in requisitos:
        if not isinstance(requisito, str) or not RE_ID_REQUISITO.fullmatch(requisito):
            erros.append((caminho, f"requisito {requisito!r} fora do formato ÁREA-SUB-NNN"))
        elif requisito not in mudanca.requisitos_conhecidos:
            erros.append((caminho, f"requisito {requisito} não existe na spec viva nem no delta.md da mudança"))

    papel = frontmatter.get("papel")
    if papel not in papeis:
        erros.append((caminho, f"papel {papel!r} não existe em governanca/papeis/"))
    provedor, revisor = frontmatter.get("provedor"), frontmatter.get("revisor")
    for campo, valor in (("provedor", provedor), ("revisor", revisor)):
        if valor not in PROVEDORES:
            erros.append((caminho, f"{campo} deve ser um de: {', '.join(sorted(PROVEDORES))}"))
    if provedor in PROVEDORES and provedor == revisor:
        erros.append((caminho, "revisor deve ser de outro provedor (quem constrói não revisa)"))

    estado = frontmatter.get("estado")
    if estado not in ESTADOS_TAREFA:
        erros.append((caminho, f"estado {estado!r} inválido; use: {', '.join(sorted(ESTADOS_TAREFA))}"))
        estado = None
    if estado in EXIGEM_PERMISSAO:
        _validar_registro_humano(frontmatter.get("permissao"), "permissao", caminho, erros)
        if mudanca.estado not in EXIGEM_APROVACAO:
            erros.append((caminho, f"tarefa em {estado} exige a proposta da mudança aprovada (gate G1)"))
    if estado == "bloqueada" and not _texto_nao_vazio(frontmatter.get("bloqueio")):
        erros.append((caminho, "tarefa bloqueada exige bloqueio (condição de desbloqueio)"))
    if mudanca.arquivada and estado is not None and estado not in ENCERRADOS:
        erros.append((caminho, "tarefa de mudança arquivada deve estar concluida ou cancelada"))

    depende_de = frontmatter.get("depende_de")
    if depende_de is None:
        depende_de = []
    if not isinstance(depende_de, list) or not all(isinstance(d, str) for d in depende_de):
        erros.append((caminho, "depende_de deve ser uma lista de IDs T-NNN"))
        depende_de = []

    orca = frontmatter.get("orca_dispatch")
    if orca is not None and not _texto_nao_vazio(orca):
        erros.append((caminho, "orca_dispatch deve ser null ou o dispatch_id do Orca"))

    _validar_casos(frontmatter.get("casos_aprovados"), raiz, caminho, erros)
    _validar_evidencias(frontmatter.get("evidencias"), requisitos, estado, mudanca.pasta, caminho, erros)

    if identificador is None:
        return None
    return Tarefa(identificador, caminho, estado, depende_de)


def _validar_dependencias(tarefas: list[Tarefa], erros: Erros) -> None:
    por_id: dict[str, Tarefa] = {}
    for tarefa in tarefas:
        if tarefa.id in por_id:
            erros.append((tarefa.caminho, f"id {tarefa.id} duplicado (também em {por_id[tarefa.id].caminho})"))
        else:
            por_id[tarefa.id] = tarefa

    for tarefa in tarefas:
        for dependencia in tarefa.depende_de:
            alvo = por_id.get(dependencia)
            if alvo is None:
                erros.append((tarefa.caminho, f"depende_de {dependencia}, que não existe"))
            elif tarefa.estado == "concluida" and alvo.estado != "concluida":
                erros.append((tarefa.caminho, f"tarefa concluida depende de {dependencia}, que está {alvo.estado}"))

    visitando, visitadas = set(), set()
    ciclos: set[frozenset[str]] = set()

    def visitar(no: str, trilha: list[str]) -> None:
        visitando.add(no)
        trilha.append(no)
        for dependencia in por_id[no].depende_de:
            if dependencia not in por_id:
                continue
            if dependencia in visitando:
                ciclo = trilha[trilha.index(dependencia) :] + [dependencia]
                if (chave := frozenset(ciclo)) not in ciclos:
                    ciclos.add(chave)
                    erros.append((por_id[dependencia].caminho, "ciclo de dependências: " + " → ".join(ciclo)))
            elif dependencia not in visitadas:
                visitar(dependencia, trilha)
        trilha.pop()
        visitando.discard(no)
        visitadas.add(no)

    for identificador in sorted(por_id):
        if identificador not in visitadas:
            visitar(identificador, [])


def validar(raiz: Path) -> Resultado:
    governanca = raiz / "governanca"
    if not governanca.is_dir():
        return Resultado([f"{raiz}: pasta governanca/ não encontrada"], 0, 0, 0)

    erros: Erros = []
    papeis = {p.stem for p in (governanca / "papeis").glob("*.md") if p.name != "README.md"}
    specs = _coletar_specs(governanca, erros)

    raiz_mudancas = governanca / "mudancas"
    arquivo = raiz_mudancas / "arquivo"
    pastas: list[tuple[Path, bool]] = []
    if raiz_mudancas.is_dir():
        pastas += [(p, False) for p in sorted(raiz_mudancas.iterdir()) if p.is_dir() and p != arquivo]
    if arquivo.is_dir():
        pastas += [(p, True) for p in sorted(arquivo.iterdir()) if p.is_dir()]

    tarefas: list[Tarefa] = []
    for pasta, arquivada in pastas:
        mudanca = _validar_mudanca(pasta, arquivada, specs, erros)
        for caminho in sorted((pasta / "tarefas").glob("*.md")):
            if (tarefa := _validar_tarefa(caminho, mudanca, papeis, raiz, erros)) is not None:
                tarefas.append(tarefa)
    _validar_dependencias(tarefas, erros)

    def relativo(caminho: Path) -> str:
        return str(caminho.relative_to(raiz)) if caminho.is_relative_to(raiz) else str(caminho)

    mensagens = [f"{relativo(caminho)}: {mensagem}" for caminho, mensagem in erros]
    return Resultado(mensagens, len(pastas), len(tarefas), len(specs))


def _achar_raiz(inicio: Path) -> Path:
    for candidato in (inicio, *inicio.parents):
        if (candidato / "governanca").is_dir():
            return candidato
    return inicio


def main(argv: list[str] | None = None) -> int:
    argumentos = sys.argv[1:] if argv is None else argv
    raiz = Path(argumentos[0]).resolve() if argumentos else _achar_raiz(Path.cwd().resolve())
    resultado = validar(raiz)
    for mensagem in resultado.erros:
        print(mensagem, file=sys.stderr)
    if resultado.erros:
        print(f"governança: {len(resultado.erros)} erro(s)", file=sys.stderr)
        return 1
    print(
        f"governança OK: {resultado.mudancas} mudança(s), {resultado.tarefas} tarefa(s), "
        f"{resultado.requisitos} requisito(s) na spec viva"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
