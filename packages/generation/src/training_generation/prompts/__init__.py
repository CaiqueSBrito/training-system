"""Prompts e rubrica versionados no pacote, fonte offline explícita (ADR-0016).

`manifest.json` fixa, para cada prompt, a versão, o arquivo e o SHA-256 dos bytes. Arquivo
alterado sem atualizar versão e hash, ausente ou fora do manifesto é erro: nunca há prompt
improvisado. Esta fonte não registra publicação nem label do Langfuse.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

PROMPT_NAMES = ("input_judge", "classifier", "pedagogue", "generation", "output_judge", "output_rubric")
SOURCE_OFFLINE = "offline"
_DIR = Path(__file__).parent


class PromptArtifactError(RuntimeError):
    """Artefato de prompt ausente, divergente do manifesto ou inválido."""


@dataclass(frozen=True)
class PromptArtifact:
    name: str
    version: str
    sha256: str
    text: str
    source: str = SOURCE_OFFLINE

    @property
    def reference(self) -> str:
        """Identidade gravada na linhagem e nos spans: origem, versão e hash dos bytes usados."""
        return f"{self.source}:{self.version}@sha256:{self.sha256}"


class PromptSet(Mapping[str, PromptArtifact]):
    def __init__(self, artifacts: Mapping[str, PromptArtifact]):
        self._artifacts = dict(artifacts)

    def __getitem__(self, name: str) -> PromptArtifact:
        return self._artifacts[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._artifacts)

    def __len__(self) -> int:
        return len(self._artifacts)


def load_prompts(directory: Path | str | None = None) -> PromptSet:
    base = Path(directory) if directory is not None else _DIR
    try:
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PromptArtifactError("manifesto de prompts ausente ou inválido") from None
    if not isinstance(manifest, dict):
        raise PromptArtifactError("manifesto de prompts inválido")
    faltando = [n for n in PROMPT_NAMES if n not in manifest]
    if faltando:
        raise PromptArtifactError(f"prompts ausentes do manifesto: {', '.join(faltando)}")
    extras = sorted(set(manifest) - set(PROMPT_NAMES))
    if extras:
        raise PromptArtifactError(f"prompts desconhecidos no manifesto: {', '.join(extras)}")
    artifacts = {}
    for name in PROMPT_NAMES:
        entrada = manifest[name]
        if not isinstance(entrada, dict) or not all(
                isinstance(entrada.get(k), str) and entrada[k].strip() for k in ("version", "file", "sha256")):
            raise PromptArtifactError(f"entrada inválida no manifesto: {name}")
        arquivo = (base / entrada["file"]).resolve()
        if arquivo.parent != base.resolve():
            raise PromptArtifactError(f"arquivo fora do diretório de prompts: {name}")
        try:
            dados = arquivo.read_bytes()
        except OSError:
            raise PromptArtifactError(f"arquivo de prompt ausente: {name}") from None
        digest = hashlib.sha256(dados).hexdigest()
        if digest != entrada["sha256"]:
            raise PromptArtifactError(f"hash divergente do manifesto: {name}")
        try:
            texto = dados.decode("utf-8")
        except UnicodeDecodeError:
            raise PromptArtifactError(f"prompt não é UTF-8: {name}") from None
        if not texto.strip():
            raise PromptArtifactError(f"prompt vazio: {name}")
        artifacts[name] = PromptArtifact(name=name, version=entrada["version"], sha256=digest, text=texto)
    return PromptSet(artifacts)
