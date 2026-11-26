"""Ambiente Alembic: Connection compartilhada ou .env da aplicação."""

from alembic import context
from training_lineage.config import create_lineage_engine, database_url
from training_lineage.tables import metadata


def run(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    run(context.config.attributes["connection"])
else:
    engine = create_lineage_engine()
    try:
        with engine.connect() as connection:
            run(connection)
    finally:
        engine.dispose()
