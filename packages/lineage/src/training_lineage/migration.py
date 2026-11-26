"""Execução Alembic, inclusive a partir do pacote instalado."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.runtime.migration import MigrationContext
from alembic.util.exc import CommandError
from alembic.script.revision import ResolutionError
from sqlalchemy import Engine


def migrate(engine: Engine, target: str = "head") -> None:
    """Alvo explícito exato; sobe ou desce conforme a revisão atual, atomicamente.

    Não aceita abreviações, intervalos ou deslocamentos relativos. 'base' remove
    o esquema; nunca é inferido de alvo desconhecido ou resolução ambígua.
    """
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    script = ScriptDirectory.from_config(config)
    try:
        if not isinstance(target, str) or not target or target.strip() != target:
            raise ValueError()
        destination = None if target == 'base' else script.get_revision(target)
        if target != 'base' and (destination is None or
                (target != 'head' and destination.revision != target)):
            raise ValueError()
        target_id = destination.revision if destination else None
    except (CommandError, ResolutionError, ValueError):
        raise ValueError('Revisão de migração inválida') from None
    with engine.begin() as connection:
        heads = MigrationContext.configure(connection).get_current_heads()
        if len(heads) > 1:
            raise ValueError('Revisão de migração inválida: múltiplas revisões atuais')
        current = heads[0] if heads else None
        try:
            if current is not None:
                script.get_revision(current)
            if current == target_id:
                return
            ancestors_target = {r.revision for r in script.walk_revisions(base='base', head=target_id)} if target_id else set()
            ancestors_current = {r.revision for r in script.walk_revisions(base='base', head=current)} if current else set()
            if current is None or current in ancestors_target:
                direction = command.upgrade
            elif target_id is None or target_id in ancestors_current:
                direction = command.downgrade
            else:
                raise ValueError()
        except (CommandError, ResolutionError, ValueError):
            raise ValueError('Revisão de migração inválida') from None
        config.attributes["connection"] = connection
        direction(config, target_id or 'base')
